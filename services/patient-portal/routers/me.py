import os
import httpx
from fastapi import APIRouter, HTTPException, Depends
from auth import require_auth
import proxy
from database import get_db, rows_to_list
from svc_token import get_service_token

router = APIRouter(prefix="/api/me", tags=["me"])

OPENMRS_URL  = os.environ.get("OPENMRS_URL",  "http://openmrs:8080")
OPENELIS_URL = os.environ.get("OPENELIS_URL", "http://openelis:8080")
RIS_URL      = os.environ.get("RIS_URL",       "http://ris:8002/api")

_OMRS_FHIR = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
_OE_FHIR   = f"{OPENELIS_URL}/fhir/R4"
_FHIR_HDR  = {"Accept": "application/fhir+json"}


async def _fhir_get(url: str, params: dict = None) -> dict:
    try:
        token = await get_service_token()
        hdrs  = {**_FHIR_HDR, "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, params=params, headers=hdrs)
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


@router.get("")
async def get_me(session: dict = Depends(require_auth)):
    patient_uuid = session["patient_id"]
    data = await _fhir_get(f"{_OMRS_FHIR}/Patient/{patient_uuid}")
    if not data:
        raise HTTPException(503, "Health records temporarily unavailable")

    name   = data.get("name", [{}])[0]
    given  = " ".join(name.get("given", []))
    family = name.get("family", "")
    return {
        "id":        patient_uuid,
        "mrn":       session.get("patient_mrn"),
        "firstname": given,
        "lastname":  family,
        "birthdate": data.get("birthDate"),
        "sex":       data.get("gender"),
    }


@router.get("/appointments")
async def get_appointments(session: dict = Depends(require_auth)):
    bundle = await _fhir_get(f"{_OMRS_FHIR}/Encounter",
                             {"patient": session["patient_id"], "_count": "50"})
    return [
        {
            "id":     e["resource"].get("id"),
            "status": e["resource"].get("status"),
            "date":   (e["resource"].get("period") or {}).get("start"),
            "type":   (e["resource"].get("type") or [{}])[0].get("text"),
        }
        for e in bundle.get("entry", [])
    ]


@router.post("/appointments/request", status_code=201)
async def request_appointment(body: dict, session: dict = Depends(require_auth)):
    dept    = (body.get("department") or "").strip()
    pref_dt = (body.get("preferred_date") or "").strip()
    reason  = (body.get("reason") or "").strip()
    if not dept:
        raise HTTPException(400, "department required")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO appointment_requests"
            "(patient_id,patient_mrn,department,preferred_date,reason)"
            " VALUES(?,?,?,?,?)",
            (session["patient_id"], session["patient_mrn"],
             dept, pref_dt or None, reason or None)
        )
    return {"status": "ok", "request_id": cur.lastrowid,
            "message": "Appointment request submitted successfully"}


@router.get("/appointments/requests")
async def get_appointment_requests(session: dict = Depends(require_auth)):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM appointment_requests WHERE patient_id=?"
            " ORDER BY created_at DESC",
            (session["patient_id"],)
        ).fetchall()
    return rows_to_list(rows)


@router.get("/results")
async def get_results(session: dict = Depends(require_auth)):
    """Completed lab results from OpenELIS FHIR."""
    bundle = await _fhir_get(
        f"{_OE_FHIR}/DiagnosticReport",
        {"patient": session["patient_id"], "status": "final", "_count": "50"})
    return [
        {
            "id":        e["resource"].get("id"),
            "status":    e["resource"].get("status"),
            "issued":    e["resource"].get("issued"),
            "code":      (e["resource"].get("code") or {}).get("text"),
            "conclusion": e["resource"].get("conclusion"),
        }
        for e in bundle.get("entry", [])
    ]


@router.get("/imaging")
async def get_imaging(session: dict = Depends(require_auth)):
    """Radiology orders + FINAL reports from RIS, filtered to this patient."""
    orders = await proxy.get(f"{RIS_URL}/orders")
    if not orders:
        return []
    patient_mrn = session.get("patient_mrn")
    results = []
    for o in orders:
        if o.get("mrn") != patient_mrn:
            continue
        if o.get("status") != "COMPLETED":
            continue
        entry = {k: v for k, v in o.items()
                 if k in {"id", "modality", "body_part", "status",
                           "accession_number", "created_at", "updated_at"}}
        report = await proxy.get(f"{RIS_URL}/reports/order/{o['id']}")
        if report and report.get("status") == "FINAL":
            entry["report"] = {
                "impression":     report.get("impression"),
                "recommendation": report.get("recommendation"),
                "finalized_at":   report.get("finalized_at"),
            }
        results.append(entry)
    return results


@router.get("/diagnoses")
async def get_diagnoses(session: dict = Depends(require_auth)):
    bundle = await _fhir_get(f"{_OMRS_FHIR}/Condition",
                             {"patient": session["patient_id"], "_count": "50"})
    return [
        {
            "id":          e["resource"].get("id"),
            "icd10code":   ((e["resource"].get("code") or {}).get("coding") or [{}])[0].get("code"),
            "description": (e["resource"].get("code") or {}).get("text"),
            "status":      (e["resource"].get("clinicalStatus") or {}).get("coding", [{}])[0].get("code"),
            "createdat":   e["resource"].get("recordedDate"),
        }
        for e in bundle.get("entry", [])
    ]


@router.get("/allergies")
async def get_allergies(session: dict = Depends(require_auth)):
    bundle = await _fhir_get(f"{_OMRS_FHIR}/AllergyIntolerance",
                             {"patient": session["patient_id"], "_count": "50"})
    return [
        {
            "id":        e["resource"].get("id"),
            "substance": (e["resource"].get("code") or {}).get("text"),
            "reaction":  (((e["resource"].get("reaction") or [{}])[0]).get("manifestation") or [{}])[0].get("text"),
            "severity":  ((e["resource"].get("reaction") or [{}])[0]).get("severity"),
        }
        for e in bundle.get("entry", [])
    ]


@router.get("/billing")
async def get_billing(session: dict = Depends(require_auth)):
    """Billing data placeholder — Odoo integration wired in Phase 5 follow-up."""
    return []


@router.get("/summary")
async def get_summary(session: dict = Depends(require_auth)):
    pid = session["patient_id"]
    enc_bundle = await _fhir_get(f"{_OMRS_FHIR}/Encounter",
                                 {"patient": pid, "status": "planned",
                                  "_count": "10", "_sort": "date"})
    lab_bundle = await _fhir_get(f"{_OE_FHIR}/DiagnosticReport",
                                 {"patient": pid, "status": "preliminary",
                                  "_count": "0", "_summary": "count"})
    upcoming       = enc_bundle.get("entry", [])
    pending_results = lab_bundle.get("total", 0)
    return {
        "upcoming_appointments": len(upcoming),
        "pending_results":       pending_results,
        "unpaid_bills":          0,
        "total_due":             0.0,
        "next_appointment": {
            "date": (upcoming[0]["resource"].get("period") or {}).get("start"),
            "type": (upcoming[0]["resource"].get("type") or [{}])[0].get("text"),
        } if upcoming else None,
    }
