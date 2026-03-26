import asyncio
from fastapi import APIRouter
from app.services import openmrs, openelis, odoo

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
