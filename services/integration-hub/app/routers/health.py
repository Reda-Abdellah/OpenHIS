import asyncio
import time
import httpx
from fastapi import APIRouter
from app.services import openmrs, openelis, odoo
from app import registry

router = APIRouter()


@router.get("/api/health")
async def health():
    omrs_ok, oe_ok, odoo_ok = await asyncio.gather(
        openmrs.health_check(),
        openelis.health_check(),
        odoo.async_health_check(),
    )
    upstreams = {
        "openmrs":  "up" if omrs_ok else "down",
        "openelis": "up" if oe_ok   else "down",
        "odoo":     "up" if odoo_ok else "down",
    }
    overall = "ok" if all([omrs_ok, oe_ok]) else "degraded"
    return {"status": overall, "service": "integration-hub", "upstreams": upstreams}


@router.get("/api/registry")
def get_registry():
    """Return all registered service manifests."""
    return {"services": registry.all_services()}


@router.get("/api/platform/status")
async def platform_status():
    """
    Fan-out health check to every registered service.
    Each service is probed at http://{name}:{port}{health_path}.
    Returns an aggregated status plus per-service detail.
    """
    services = registry.all_services()

    async def _probe(svc: dict) -> dict:
        name        = svc.get("name", "unknown")
        port        = svc.get("port", 80)
        health_path = svc.get("health_path", "/api/health")
        url         = f"http://{name}:{port}{health_path}"
        t0          = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(url)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            return {
                "name":        name,
                "status":      "up" if r.status_code == 200 else "degraded",
                "http_status": r.status_code,
                "latency_ms":  elapsed_ms,
                "display_name": svc.get("display_name", name),
                "profile":      svc.get("profile", ""),
            }
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            return {
                "name":        name,
                "status":      "down",
                "error":       str(exc),
                "latency_ms":  elapsed_ms,
                "display_name": svc.get("display_name", name),
                "profile":      svc.get("profile", ""),
            }

    results = await asyncio.gather(*[_probe(s) for s in services])
    overall = "ok" if all(r["status"] == "up" for r in results) else "degraded"
    return {"status": overall, "services": results}
