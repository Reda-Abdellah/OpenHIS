"""
FHIR CapabilityStatement endpoint.

GET /fhir/metadata returns an R4 CapabilityStatement describing how the
integration hub moves FHIR resources. The hub is a façade/poller, not a
FHIR REST server: this is its ONLY /fhir endpoint, so no per-resource REST
interactions are declared — each resource entry instead documents the real
poll/push flow that carries it.

The endpoint is intentionally token-free (exempted from JWTMiddleware in
app/main.py): a CapabilityStatement carries no PHI, and FHIR clients fetch
/metadata before they can authenticate.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/fhir", tags=["fhir"])

# Build/revision date of this CapabilityStatement — bump when the declared
# capabilities change. A constant (not datetime.now at import/request time)
# keeps the statement deterministic and cacheable.
_BUILD_DATE = "2026-06-12"

_CAPABILITY_STATEMENT = {
    "resourceType": "CapabilityStatement",
    "id": "openhis-integration-hub",
    "status": "active",
    "date": _BUILD_DATE,
    "publisher": "OpenHIS",
    "kind": "instance",
    "fhirVersion": "4.0.1",
    "format": ["application/fhir+json", "json"],
    "software": {
        "name": "OpenHIS Integration Hub",
        "version": "1.1.0",
    },
    "description": (
        "FHIR R4 integration hub — bidirectional sync between OpenMRS, "
        "OpenELIS, and Odoo; webhook receiver for Orthanc, the RIS, and "
        "the AI controller. The hub is a lightweight FHIR façade and "
        "poller, not a FHIR REST server: GET /fhir/metadata is its only "
        "FHIR endpoint. The resource types listed below move through the "
        "hub via upstream polling and its /api/events/* webhooks; they "
        "cannot be read or written at /fhir/{type}."
    ),
    "rest": [
        {
            "mode": "server",
            "documentation": (
                "Discovery-only REST surface: the implicit 'capabilities' "
                "interaction (GET /fhir/metadata) is the sole FHIR "
                "endpoint this server exposes. No per-resource REST "
                "interactions are declared because none are served — each "
                "resource entry's documentation describes the poll/push "
                "flow that actually carries it."
            ),
            "resource": [
                {
                    "type": "Patient",
                    "documentation": (
                        "Polled from the OpenMRS FHIR R4 API by the "
                        "background worker and synced to OpenELIS and "
                        "Odoo. Not readable or writable here."
                    ),
                },
                {
                    "type": "ServiceRequest",
                    "documentation": (
                        "Lab orders polled from OpenMRS and routed to "
                        "OpenELIS by the background worker. Not readable "
                        "or writable here."
                    ),
                },
                {
                    "type": "DiagnosticReport",
                    "documentation": (
                        "Lab results polled from OpenELIS and pushed to "
                        "OpenMRS; FINAL radiology reports arrive on the "
                        "POST /api/events/report-final webhook and are "
                        "pushed to OpenMRS. Not readable or writable here."
                    ),
                },
                {
                    "type": "ImagingStudy",
                    "documentation": (
                        "Built from Orthanc DICOM metadata received on "
                        "the POST /api/events/dicom-stored webhook and "
                        "pushed to OpenMRS. Not readable or writable here."
                    ),
                },
                {
                    "type": "Observation",
                    "documentation": (
                        "AI results received on the POST "
                        "/api/events/ai-job-completed webhook are pushed "
                        "to OpenMRS as Observations. Not readable or "
                        "writable here."
                    ),
                },
            ],
        }
    ],
}


@router.get("/metadata", summary="FHIR R4 CapabilityStatement")
async def capability_statement():
    """Return a FHIR R4 CapabilityStatement for this integration hub."""
    return JSONResponse(
        content=_CAPABILITY_STATEMENT,
        media_type="application/fhir+json",
    )
