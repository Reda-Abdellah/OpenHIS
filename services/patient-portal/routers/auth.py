import os
import httpx
from fastapi import APIRouter, HTTPException, Header
from auth import create_session, delete_session, validate_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

OPENMRS_URL  = os.environ.get("OPENMRS_URL",  "http://openmrs:8080")
OPENMRS_USER = os.environ.get("OPENMRS_USER", "admin")
OPENMRS_PASS = os.environ.get("OPENMRS_PASS", "Admin123")
_FHIR        = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
_AUTH        = (OPENMRS_USER, OPENMRS_PASS)


@router.post("/login")
async def login(body: dict):
    mrn = (body.get("mrn") or "").strip()
    dob = (body.get("birthdate") or "").strip()
    if not mrn or not dob:
        raise HTTPException(400, "mrn and birthdate required")

    # Search OpenMRS FHIR for patient by MRN identifier
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=10) as c:
            r = await c.get(f"{_FHIR}/Patient",
                            params={"identifier": mrn, "_count": "1"},
                            headers={"Accept": "application/fhir+json"})
        entries = r.json().get("entry", []) if r.status_code == 200 else []
    except Exception:
        raise HTTPException(503, "Health records temporarily unavailable")

    if not entries:
        raise HTTPException(401, "Invalid credentials")

    patient      = entries[0]["resource"]
    stored_dob   = (patient.get("birthDate") or "").strip()  # FHIR format: YYYY-MM-DD
    if stored_dob != dob:
        raise HTTPException(401, "Invalid credentials")

    patient_uuid = patient["id"]
    name_parts   = patient.get("name", [{}])[0]
    given        = " ".join(name_parts.get("given", []))
    family       = name_parts.get("family", "")
    patient_name = f"{given} {family}".strip() or mrn

    token = create_session(patient_uuid, mrn, patient_name)
    return {"token": token, "patient_id": patient_uuid, "patient_name": patient_name}


@router.post("/logout")
async def logout(body: dict = None):
    token = ((body or {}).get("token") or "").strip()
    if token:
        delete_session(token)
    return {"status": "ok"}


@router.get("/validate")
async def validate_token(authorization: str = Header(default=None)):
    if not authorization:
        raise HTTPException(401, "No token provided")
    token   = authorization.replace("Bearer ", "").strip()
    session = validate_session(token)
    if not session:
        raise HTTPException(401, "Invalid or expired session")
    return {"valid": True, "patient_name": session.get("patient_name")}
