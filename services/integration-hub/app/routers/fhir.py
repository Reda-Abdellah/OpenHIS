"""
FHIR CapabilityStatement endpoint.

GET /fhir/metadata returns an R4 CapabilityStatement describing the resource
types the integration hub handles, allowing FHIR clients to discover
capabilities before submitting resources.
"""
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/fhir", tags=["fhir"])

_CAPABILITY_STATEMENT = {
    "resourceType": "CapabilityStatement",
    "id": "openhis-integration-hub",
    "status": "active",
    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
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
        "OpenELIS, and Odoo; webhook receiver for Orthanc and the RIS."
    ),
    "rest": [
        {
            "mode": "server",
            "resource": [
                {
                    "type": "Patient",
                    "interaction": [
                        {"code": "read"},
                        {"code": "create"},
                        {"code": "update"},
                    ],
                    "versioning": "no-version",
                },
                {
                    "type": "DiagnosticReport",
                    "interaction": [
                        {"code": "read"},
                        {"code": "create"},
                    ],
                    "versioning": "no-version",
                },
                {
                    "type": "ImagingStudy",
                    "interaction": [
                        {"code": "create"},
                    ],
                    "versioning": "no-version",
                },
                {
                    "type": "Observation",
                    "interaction": [
                        {"code": "create"},
                    ],
                    "versioning": "no-version",
                },
                {
                    "type": "ServiceRequest",
                    "interaction": [
                        {"code": "read"},
                        {"code": "create"},
                    ],
                    "versioning": "no-version",
                },
                {
                    "type": "MedicationRequest",
                    "interaction": [
                        {"code": "create"},
                    ],
                    "versioning": "no-version",
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
