"""
Pulls metrics from every service and stores JSON snapshots in SQLite.
Each service maps to a domain key: ehr | orders | billing | ai | mpi | lis | ris
"""
import datetime, json, logging, os
import httpx
from database import get_db

log = logging.getLogger('analytics.collector')

EHR_URL = os.environ.get('EHR_URL',            'http://ehr:8003/api')
LIS_URL = os.environ.get('LIS_URL',            'http://lis:8004/api')
RIS_URL = os.environ.get('RIS_URL',            'http://ris:8002/api')
AI_URL  = os.environ.get('AI_CONTROLLER_URL',  'http://ai-controller:8000/api')
MPI_URL = os.environ.get('MPI_URL',            'http://mpi:8007/api')

_LAST_REFRESH: dict = {}   # domain → captured_at


async def _get(client, url):
    try:
        r = await client.get(url, timeout=8.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"GET {url} → {type(e).__name__}: {e}")
        return None


def _tat_hours(items, created='createdat', updated='updatedat',
               status_field='status', done_status='COMPLETED'):
    tats = []
    for item in (items or []):
        if item.get(status_field) != done_status:
            continue
        try:
            t0 = datetime.datetime.fromisoformat(item[created])
            t1 = datetime.datetime.fromisoformat(item[updated])
            h  = (t1 - t0).total_seconds() / 3600
            if 0 < h < 720:
                tats.append(h)
        except Exception:
            pass
    return round(sum(tats) / len(tats), 2) if tats else None


def _by_status(items, field='status'):
    out = {}
    for item in (items or []):
        k = item.get(field, 'UNKNOWN')
        out[k] = out.get(k, 0) + 1
    return out


async def collect_all() -> dict:
    today  = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    result = {}

    async with httpx.AsyncClient(timeout=10.0) as c:

        # ── EHR ──────────────────────────────────────────────────────────────
        patients   = await _get(c, f"{EHR_URL}/patients")
        encounters = await _get(c, f"{EHR_URL}/encounters")
        if patients is not None:
            active_enc = [e for e in (encounters or []) if e.get('status') == 'active']
            new_today  = [p for p in patients if (p.get('createdat') or '').startswith(today)]
            ward_map   = {}
            for e in active_enc:
                w = e.get('ward') or 'Unknown'
                ward_map[w] = ward_map.get(w, 0) + 1
            result['ehr'] = {
                'total_patients':     len(patients),
                'active_encounters':  len(active_enc),
                'new_patients_today': len(new_today),
                'ward_breakdown':     ward_map,
            }

        # ── Orders & TAT ─────────────────────────────────────────────────────
        ehr_orders = await _get(c, f"{EHR_URL}/orders")
        lis_orders = await _get(c, f"{LIS_URL}/orders")
        ris_orders = await _get(c, f"{RIS_URL}/orders")
        if ehr_orders is not None:
            lab_orders = [o for o in ehr_orders if o.get('ordertype') == 'LAB']
            img_orders = [o for o in ehr_orders if o.get('ordertype') == 'IMAGING']
            result['orders'] = {
                'lab_pending':       len([o for o in lab_orders if o.get('status') == 'PENDING']),
                'lab_completed':     len([o for o in lab_orders if o.get('status') == 'COMPLETED']),
                'lab_tat_hours':     _tat_hours(lis_orders or lab_orders),
                'imaging_pending':   len([o for o in img_orders if o.get('status') == 'PENDING']),
                'imaging_completed': len([o for o in img_orders if o.get('status') == 'COMPLETED']),
                'imaging_tat_hours': _tat_hours(ris_orders or img_orders),
                'lab_by_status':     _by_status(lab_orders),
                'img_by_status':     _by_status(img_orders),
            }

        # ── Billing ───────────────────────────────────────────────────────────
        billing = await _get(c, f"{EHR_URL}/billing")
        if billing is not None:
            total   = sum(b.get('amount', 0) for b in billing)
            paid    = sum(b.get('amount', 0) for b in billing if b.get('status') == 'paid')
            partial = sum(b.get('amount', 0) for b in billing if b.get('status') == 'partial')
            result['billing'] = {
                'record_count':     len(billing),
                'total_amount':     round(total, 2),
                'paid_amount':      round(paid, 2),
                'partial_amount':   round(partial, 2),
                'pending_amount':   round(total - paid - partial, 2),
                'unpaid_count':     len([b for b in billing if b.get('status') == 'pending']),
                'collection_rate':  round(paid / total * 100, 1) if total else 0,
                'by_status':        _by_status(billing),
            }

        # ── AI Pipeline ───────────────────────────────────────────────────────
        jobs = await _get(c, f"{AI_URL}/jobs?limit=500")
        if jobs is not None:
            by_status = _by_status(jobs, 'status')
            durations = [j['durationms'] for j in jobs if j.get('durationms')]
            completed = by_status.get('COMPLETED', 0)
            total_j   = len(jobs)
            result['ai'] = {
                'total':          total_j,
                'by_status':      by_status,
                'success_rate':   round(completed / total_j * 100, 1) if total_j else 0,
                'avg_duration_ms': round(sum(durations) / len(durations)) if durations else None,
                'failed':         by_status.get('FAILED', 0),
                'running':        by_status.get('RUNNING', 0),
            }

        # ── MPI ───────────────────────────────────────────────────────────────
        mpi = await _get(c, f"{MPI_URL}/health")
        if mpi:
            result['mpi'] = {
                'master_patients':  mpi.get('master_patients', 0),
                'cross_references': mpi.get('cross_references', 0),
                'pending_matches':  mpi.get('pending_matches', 0),
            }

    return result


async def collect_and_store():
    now = datetime.datetime.utcnow().isoformat(timespec='seconds')
    try:
        data = await collect_all()
        with get_db() as db:
            for domain, payload in data.items():
                if payload is not None:
                    db.execute(
                        "INSERT INTO snapshots(domain,data,captured_at) VALUES(?,?,?)",
                        (domain, json.dumps(payload), now)
                    )
                    _LAST_REFRESH[domain] = now
            # Retain 90 days
            db.execute("DELETE FROM snapshots WHERE captured_at < datetime('now', '-90 days')")
        log.info(f"Metrics stored: domains={list(data.keys())} at {now}")
    except Exception as e:
        log.error(f"collect_and_store failed: {e}")
