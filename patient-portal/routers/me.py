import os
from fastapi import APIRouter, HTTPException, Depends
from auth     import require_auth
import proxy
from database import get_db, rows_to_list

router  = APIRouter(prefix="/api/me", tags=["me"])
EHR_URL = os.environ.get('EHR_URL', 'http://ehr:8003/api')
RIS_URL = os.environ.get('RIS_URL', 'http://ris:8002/api')

_PATIENT_FIELDS = {
    "id", "mrn", "firstname", "lastname",
    "birthdate", "sex", "phone", "insuranceid",
}


@router.get("")
async def get_me(session: dict = Depends(require_auth)):
    patient = await proxy.get(f"{EHR_URL}/patients/{session['patient_id']}")
    if not patient:
        raise HTTPException(503, "Health records temporarily unavailable")
    return {k: v for k, v in patient.items() if k in _PATIENT_FIELDS}


@router.get("/appointments")
async def get_appointments(session: dict = Depends(require_auth)):
    data = await proxy.get(
        f"{EHR_URL}/appointments?patientid={session['patient_id']}"
    )
    return data or []


@router.post("/appointments/request", status_code=201)
async def request_appointment(body: dict, session: dict = Depends(require_auth)):
    dept     = (body.get("department") or "").strip()
    pref_dt  = (body.get("preferred_date") or "").strip()
    reason   = (body.get("reason") or "").strip()
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
    """Completed LAB orders visible to patient."""
    orders = await proxy.get(
        f"{EHR_URL}/orders?patientid={session['patient_id']}&ordertype=LAB"
    )
    return [
        {k: v for k, v in o.items()
         if k in {"id", "ordertype", "orderdetail", "status",
                  "createdat", "updatedat", "priority", "patientname"}}
        for o in (orders or [])
        if o.get("status") == "COMPLETED"
    ]


@router.get("/imaging")
async def get_imaging(session: dict = Depends(require_auth)):
    """Completed IMAGING orders + FINAL RIS reports visible to patient."""
    orders = await proxy.get(
        f"{EHR_URL}/orders?patientid={session['patient_id']}&ordertype=IMAGING"
    )
    if not orders:
        return []

    results = []
    for o in orders:
        if o.get("status") != "COMPLETED":
            continue
        entry = {k: v for k, v in o.items()
                 if k in {"id", "ordertype", "orderdetail", "status",
                          "createdat", "updatedat", "patientname"}}
        # Try to fetch the FINAL radiology report from RIS
        report = await proxy.get(
            f"{RIS_URL}/reports/order/{o['id']}"
        )
        if report and report.get("status") == "FINAL":
            entry["report"] = {
                "impression":      report.get("impression"),
                "recommendation":  report.get("recommendation"),
                "finalized_at":    report.get("finalizedat"),
            }
        results.append(entry)
    return results


@router.get("/diagnoses")
async def get_diagnoses(session: dict = Depends(require_auth)):
    data = await proxy.get(
        f"{EHR_URL}/patients/{session['patient_id']}/diagnoses"
    )
    return [
        {k: v for k, v in d.items()
         if k in {"id", "icd10code", "description", "status", "createdat"}}
        for d in (data or [])
        if d.get("status") == "active"
    ]


@router.get("/allergies")
async def get_allergies(session: dict = Depends(require_auth)):
    data = await proxy.get(
        f"{EHR_URL}/patients/{session['patient_id']}/allergies"
    )
    return [
        {k: v for k, v in a.items()
         if k in {"id", "substance", "reaction", "severity"}}
        for a in (data or [])
    ]


@router.get("/billing")
async def get_billing(session: dict = Depends(require_auth)):
    data = await proxy.get(
        f"{EHR_URL}/billing?patientid={session['patient_id']}"
    )
    return [
        {k: v for k, v in b.items()
         if k in {"id", "cptcode", "description", "amount",
                  "status", "createdat"}}
        for b in (data or [])
    ]


@router.get("/summary")
async def get_summary(session: dict = Depends(require_auth)):
    """One-shot summary used by the dashboard tab."""
    pid = session["patient_id"]
    appts   = await proxy.get(f"{EHR_URL}/appointments?patientid={pid}")
    orders  = await proxy.get(f"{EHR_URL}/orders?patientid={pid}")
    billing = await proxy.get(f"{EHR_URL}/billing?patientid={pid}")

    upcoming_appts = [
        a for a in (appts or [])
        if a.get("status") == "scheduled"
    ]
    pending_results = [
        o for o in (orders or [])
        if o.get("ordertype") == "LAB" and o.get("status") == "PENDING"
    ]
    unpaid_bills = [
        b for b in (billing or [])
        if b.get("status") == "pending"
    ]
    total_due = sum(b.get("amount", 0) for b in unpaid_bills)

    return {
        "upcoming_appointments": len(upcoming_appts),
        "pending_results":       len(pending_results),
        "unpaid_bills":          len(unpaid_bills),
        "total_due":             round(total_due, 2),
        "next_appointment":      upcoming_appts[0] if upcoming_appts else None,
    }
