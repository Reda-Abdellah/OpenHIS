import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from typing import Optional
from database import get_db
from openhis_sdk.auth import require_roles

router = APIRouter(prefix="/api/metrics", tags=["metrics"])
KNOWN_DOMAINS = {'ehr', 'orders', 'lis', 'billing', 'ai', 'mpi'}

# Canonical V&V vocabulary (docs/verification_and_validation, S8) → storage
# domain. The summary exposes both so dashboards can use either name.
ALIASES = {'patients': 'ehr', 'lab': 'lis', 'imaging': 'orders'}


def _resolve(domain: str) -> str:
    return ALIASES.get(domain, domain)


def _latest(db, domain: str):
    row = db.execute(
        "SELECT data, captured_at FROM snapshots WHERE domain=? "
        "ORDER BY captured_at DESC LIMIT 1",
        (domain,)
    ).fetchone()
    if row:
        return {'data': json.loads(row['data']), 'captured_at': row['captured_at']}
    return {'data': None, 'captured_at': None}


@router.get("/summary", dependencies=[Depends(require_roles("clinician", "admin"))])
def get_summary():
    with get_db() as db:
        out = {d: _latest(db, d) for d in KNOWN_DOMAINS}
        out.update({alias: _latest(db, target) for alias, target in ALIASES.items()})
        return out


@router.get("/trends", dependencies=[Depends(require_roles("clinician", "admin"))])
def get_trends(domain: str = "ehr", metric: str = "total_patients", limit: int = 20):
    domain = _resolve(domain)
    with get_db() as db:
        rows = db.execute(
            "SELECT data, captured_at FROM snapshots WHERE domain=? "
            "ORDER BY captured_at DESC LIMIT ?",
            (domain, limit)
        ).fetchall()
    series = []
    for row in reversed(rows):
        try:
            val = json.loads(row['data']).get(metric)
            if val is not None:
                series.append({'ts': row['captured_at'], 'value': float(val)})
        except Exception:
            pass
    return {'domain': domain, 'metric': metric, 'series': series}


@router.get("/{domain}", dependencies=[Depends(require_roles("clinician", "admin"))])
def get_domain(domain: str):
    domain = _resolve(domain)
    if domain not in KNOWN_DOMAINS:
        raise HTTPException(404, f"Unknown domain {domain!r}. Known: {sorted(KNOWN_DOMAINS)}")
    with get_db() as db:
        snap = _latest(db, domain)
    if snap['data'] is None:
        raise HTTPException(404, f"No data collected yet for domain {domain!r}")
    return snap


@router.post("/refresh", status_code=202, dependencies=[Depends(require_roles("admin"))])
async def trigger_refresh(bg: BackgroundTasks):
    from collector import collect_and_store
    bg.add_task(collect_and_store)
    return {"status": "queued"}
