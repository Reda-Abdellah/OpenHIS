import json
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional
from database import get_db

router = APIRouter(prefix="/api/metrics", tags=["metrics"])
KNOWN_DOMAINS = {'ehr', 'orders', 'billing', 'ai', 'mpi'}


def _latest(db, domain: str):
    row = db.execute(
        "SELECT data, captured_at FROM snapshots WHERE domain=? "
        "ORDER BY captured_at DESC LIMIT 1",
        (domain,)
    ).fetchone()
    if row:
        return {'data': json.loads(row['data']), 'captured_at': row['captured_at']}
    return {'data': None, 'captured_at': None}


@router.get("/summary")
def get_summary():
    with get_db() as db:
        return {d: _latest(db, d) for d in KNOWN_DOMAINS}


@router.get("/trends")
def get_trends(domain: str, metric: str, limit: int = 20):
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


@router.get("/{domain}")
def get_domain(domain: str):
    if domain not in KNOWN_DOMAINS:
        raise HTTPException(404, f"Unknown domain {domain!r}. Known: {sorted(KNOWN_DOMAINS)}")
    with get_db() as db:
        snap = _latest(db, domain)
    if snap['data'] is None:
        raise HTTPException(404, f"No data collected yet for domain {domain!r}")
    return snap


@router.post("/refresh", status_code=202)
async def trigger_refresh(bg: BackgroundTasks):
    from collector import collect_and_store
    bg.add_task(collect_and_store)
    return {"status": "queued"}
