"""
Service Registry — live catalog of active OpenHIS services.

Populated via:
  - Startup: base services are seeded automatically
  - OPM CLI:  POST/DELETE calls on opm enable/disable

GET  /api/registry           — list all services with live health
GET  /api/registry/{name}    — single service + live health
POST /api/registry           — register / upsert (OPM or admin auth)
DELETE /api/registry/{name}  — deregister (OPM or admin auth)
"""
import asyncio, datetime, json, time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from security import require_admin
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/registry", tags=["registry"])


class ServiceEntry(BaseModel):
    name:         str
    profile:      str = "base"
    internal_url: str
    health_url:   str
    nginx_path:   str | None = None
    metadata:     dict = {}


# ── Base services seeded on startup ───────────────────────────────────────────
BASE_SERVICES = [
    ServiceEntry(
        name="admin", profile="base",
        internal_url="http://admin:8011",
        health_url="http://admin:8011/api/health",
        nginx_path="/admin",
    ),
    ServiceEntry(
        name="mpi", profile="base",
        internal_url="http://mpi:8007",
        health_url="http://mpi:8007/api/health",
        nginx_path="/mpi",
    ),
    ServiceEntry(
        name="integration-hub", profile="base",
        internal_url="http://integration-hub:8012",
        health_url="http://integration-hub:8012/api/health",
        nginx_path="/integration-hub",
    ),
    ServiceEntry(
        name="hl7", profile="base",
        internal_url="http://hl7:8009",
        health_url="http://hl7:8009/api/health",
        nginx_path="/hl7",
    ),
]


def seed_base_services():
    """Called at startup to register base services."""
    with get_db() as db:
        for s in BASE_SERVICES:
            db.execute(
                """INSERT OR IGNORE INTO service_registry
                   (name, profile, internal_url, health_url, nginx_path, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (s.name, s.profile, s.internal_url, s.health_url,
                 s.nginx_path, json.dumps(s.metadata)),
            )


# ── Health probe ───────────────────────────────────────────────────────────────

async def _probe(entry: dict) -> dict:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(entry["health_url"])
            ms = round((time.monotonic() - t0) * 1000)
            status = "online" if r.status_code < 400 else "degraded"
            return {**entry, "status": status, "response_ms": ms}
    except Exception as e:
        ms = round((time.monotonic() - t0) * 1000)
        return {**entry, "status": "offline", "response_ms": ms,
                "error": str(e)[:80]}


def _update_status(name: str, status: str):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "UPDATE service_registry SET status=?, last_seen=? WHERE name=?",
            (status, now if status == "online" else None, name),
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_services(_: dict = Depends(require_admin)):
    with get_db() as db:
        rows = rows_to_list(
            db.execute("SELECT * FROM service_registry ORDER BY profile, name").fetchall()
        )
    results = await asyncio.gather(*[_probe(r) for r in rows])
    for r in results:
        _update_status(r["name"], r["status"])
    online  = sum(1 for r in results if r["status"] == "online")
    offline = sum(1 for r in results if r["status"] == "offline")
    return {
        "services": results,
        "online": online, "offline": offline,
        "degraded": len(results) - online - offline,
        "total": len(results),
        "checked_at": datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }


@router.get("/{name}")
async def get_service(name: str, _: dict = Depends(require_admin)):
    with get_db() as db:
        row = row_to_dict(
            db.execute("SELECT * FROM service_registry WHERE name=?", (name,)).fetchone()
        )
    if not row:
        raise HTTPException(404, f"Service '{name}' not in registry")
    result = await _probe(row)
    _update_status(name, result["status"])
    return result


@router.post("", status_code=201)
async def register_service(entry: ServiceEntry, _: dict = Depends(require_admin)):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            """INSERT INTO service_registry
               (name, profile, internal_url, health_url, nginx_path, metadata, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 profile=excluded.profile,
                 internal_url=excluded.internal_url,
                 health_url=excluded.health_url,
                 nginx_path=excluded.nginx_path,
                 metadata=excluded.metadata,
                 registered_at=excluded.registered_at""",
            (entry.name, entry.profile, entry.internal_url, entry.health_url,
             entry.nginx_path, json.dumps(entry.metadata), now),
        )
    return {"registered": entry.name}


@router.delete("/{name}", status_code=200)
async def deregister_service(name: str, _: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("DELETE FROM service_registry WHERE name=?", (name,))
    return {"deregistered": name}
