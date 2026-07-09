"""Hub-mediated context reads for native services.

Native services must not talk to OpenELIS/OpenMRS directly (adapter
rule): this surface performs the read through the hub's single adapter
and writes an audit row. Consumer today: the hl7 gateway's
``lab_result.ready`` bus handler, which resolves the DiagnosticReport
it turns into an outbound ORU^R01.

Machine-to-machine: gated with the ``internal-sync`` role (same pattern
as the /api/events ingest gates, T-06); ``admin`` is accepted for
operator debugging.

Failure model: upstream fetch failures surface as 404 (resource
unavailable), never 5xx — callers degrade gracefully.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from openhis_sdk.auth import require_roles

from app.db import audit
from app.services import openelis

log = logging.getLogger("hub.context")

router = APIRouter(prefix="/api/context", tags=["context"])

_GATE = [Depends(require_roles("internal-sync", "admin"))]


@router.get("/diagnostic-report/{oe_id}", dependencies=_GATE)
async def get_diagnostic_report(oe_id: str) -> dict:
    """Resolve an OpenELIS DiagnosticReport by id (audited hub read)."""
    report = await openelis.get_diagnostic_report(oe_id)
    if report is None:
        raise HTTPException(status_code=404, detail="DiagnosticReport unavailable")
    await audit.log_event(
        "context_read", "DiagnosticReport", oe_id, "oe→hub", "ok",
    )
    return {"diagnostic_report": report}
