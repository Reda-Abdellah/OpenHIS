import csv, io, json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from database import get_db

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/{domain}")
def export_domain(domain: str, limit: int = 90):
    """Download all snapshots for a domain as CSV (newest first)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT data, captured_at FROM snapshots WHERE domain=? "
            "ORDER BY captured_at DESC LIMIT ?",
            (domain, limit)
        ).fetchall()
    if not rows:
        raise HTTPException(404, f"No data for domain {domain!r}")

    records = []
    for row in rows:
        try:
            flat = {'captured_at': row['captured_at']}
            data = json.loads(row['data'])
            for k, v in data.items():
                flat[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
            records.append(flat)
        except Exception:
            pass

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=records[0].keys() if records else ['captured_at'])
    writer.writeheader()
    writer.writerows(records)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={domain}_metrics.csv"}
    )
