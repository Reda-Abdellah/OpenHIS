import os
from fastapi import APIRouter, HTTPException, Header
import proxy as _proxy
from auth import create_session, delete_session, validate_session

router  = APIRouter(prefix="/api/auth", tags=["auth"])
EHR_URL = os.environ.get('EHR_URL', 'http://ehr:8003/api')

# Aliased so tests can patch at the module level
proxy_get = _proxy.get


@router.post("/login")
async def login(body: dict):
    mrn = (body.get("mrn") or "").strip()
    dob = (body.get("birthdate") or "").strip()
    if not mrn or not dob:
        raise HTTPException(400, "mrn and birthdate required")

    patients = await proxy_get(f"{EHR_URL}/patients?q={mrn}")
    if not patients:
        raise HTTPException(401, "Invalid credentials")

    patient = next((p for p in patients if p.get("mrn") == mrn), None)
    if not patient:
        raise HTTPException(401, "Invalid credentials")

    stored_dob = (patient.get("birthdate") or "").strip()
    if stored_dob != dob:
        raise HTTPException(401, "Invalid credentials")

    patient_name = (
        f"{patient.get('firstname','')} {patient.get('lastname','')}".strip()
    )
    token = create_session(patient["id"], mrn, patient_name)
    return {
        "token":        token,
        "patient_id":   patient["id"],
        "patient_name": patient_name,
    }


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
