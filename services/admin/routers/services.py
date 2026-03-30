"""
Service health — delegates to the registry router for live checks.
Kept for backward-compatibility; the registry router is now the source of truth.
"""
import asyncio, datetime, time
import httpx
from fastapi import APIRouter, Depends
from jwt_auth import require_token
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/services", tags=["services"])


async def _check(name: str, url: str, path: str | None) -> dict:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r   = await c.get(url)
            ms  = round((time.monotonic() - t0) * 1000)
            data = {}
            try:
                data = r.json()
            except Exception:
                pass
            status = "online" if r.status_code < 400 else "degraded"
            return {"name": name, "url": url, "path": path,
                    "status": status, "http_status": r.status_code,
                    "response_ms": ms, "data": data}
    except Exception as e:
        ms = round((time.monotonic() - t0) * 1000)
        return {"name": name, "url": url, "path": path,
                "status": "offline", "response_ms": ms,
                "error": str(e)[:80]}


@router.get("")
async def get_services(_: dict = Depends(require_token)):
    """
    Health check all registered services.
    Pulls the service list from the registry so it automatically reflects
    which services OPM has enabled.
    """
    with get_db() as db:
        rows = rows_to_list(
            db.execute(
                "SELECT name, health_url, nginx_path FROM service_registry ORDER BY profile, name"
            ).fetchall()
        )

    tasks   = [_check(r["name"], r["health_url"], r.get("nginx_path")) for r in rows]
    results = await asyncio.gather(*tasks)
    online  = sum(1 for r in results if r["status"] == "online")
    offline = sum(1 for r in results if r["status"] == "offline")
    degraded= sum(1 for r in results if r["status"] == "degraded")
    return {
        "services":   results,
        "online":     online,
        "offline":    offline,
        "degraded":   degraded,
        "total":      len(results),
        "checked_at": datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }
