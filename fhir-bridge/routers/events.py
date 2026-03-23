"""
Event bus: receives internal domain events, translates to FHIR, routes cross-service.
"""
import os, logging
import httpx
from fastapi import APIRouter, BackgroundTasks
from translators.patient import to_fhir_patient
from translators.service_request import to_fhir_service_request
from translators.diagnostic_report import (
    to_fhir_diagnostic_report_lab,
    to_fhir_diagnostic_report_radiology,
)
from translators.imaging_study import to_fhir_imaging_study
from translators.observation import to_fhir_observations_from_ai

router = APIRouter(prefix="/api/events", tags=["events"])
log = logging.getLogger("fhir-bridge.events")

EHR_URL         = os.environ.get("EHR_URL",           "http://ehr:8003/api")
RIS_URL         = os.environ.get("RIS_URL",           "http://ris:8002/api")
LIS_URL         = os.environ.get("LIS_URL",           "http://lis:8004/api")
ORTHANC_URL     = os.environ.get("ORTHANC_URL",       "http://orthanc:8042")
AI_URL          = os.environ.get("AI_CONTROLLER_URL", "http://ai-controller:8000/api")
FHIR_SERVER_URL = os.environ.get("FHIR_SERVER_URL",   "")
FHIR_ENABLED    = os.environ.get("FHIR_ENABLED", "true").lower() == "true"


# ── helpers ────────────────────────────────────────────────────────────────────

async def _push_to_fhir(resource: dict):
    if not FHIR_ENABLED or not FHIR_SERVER_URL:
        return
    rtype = resource.get("resourceType", "Resource")
    rid   = resource.get("id")
    url   = f"{FHIR_SERVER_URL}/{rtype}/{rid}" if rid else f"{FHIR_SERVER_URL}/{rtype}"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.put(url, json=resource,
                            headers={"Content-Type": "application/fhir+json"})
            r.raise_for_status()
            log.info(f"FHIR PUT {rtype}/{rid} → {r.status_code}")
    except Exception as e:
        log.warning(f"FHIR push failed {rtype}/{rid}: {e}")


async def _post(url: str, payload: dict):
    """Fire-and-forget POST."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(url, json=payload)
            log.info(f"POST {url} → {r.status_code}")
    except Exception as e:
        log.warning(f"POST {url} failed: {e}")


async def _patch(url: str, payload: dict):
    """Fire-and-forget PATCH — used for EHR order write-backs."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.patch(url, json=payload)
            log.info(f"PATCH {url} → {r.status_code}")
    except Exception as e:
        log.warning(f"PATCH {url} failed: {e}")


# ── event handlers ─────────────────────────────────────────────────────────────

@router.post("/patient-created")
async def on_patient_created(payload: dict, bg: BackgroundTasks):
    """EHR → Notify RIS + LIS to upsert patient. Push FHIR Patient."""
    bg.add_task(_handle_patient_created, payload)
    return {"status": "queued"}


async def _handle_patient_created(payload: dict):
    fhir_patient = to_fhir_patient(payload)
    await _push_to_fhir(fhir_patient)
    # Sync to RIS
    await _post(f"{RIS_URL}/patients/from-ehr", {
        "ehr_id":       payload.get("id"),
        "mrn":          payload.get("mrn"),
        "patient_name": f"{payload.get('last_name', '')}, {payload.get('first_name', '')}",
        "birth_date":   payload.get("birth_date"),
        "sex":          payload.get("sex"),
    })
    # Sync to LIS
    await _post(f"{LIS_URL}/lab-patients", {
        "ehr_patient_id": payload.get("id"),
        "patient_name":   f"{payload.get('last_name', '')}, {payload.get('first_name', '')}",
        "birth_date":     payload.get("birth_date"),
        "mrn":            payload.get("mrn"),
    })


@router.post("/imaging-order")
async def on_imaging_order(payload: dict, bg: BackgroundTasks):
    """EHR imaging order → create RIS order. Push FHIR ServiceRequest."""
    bg.add_task(_handle_imaging_order, payload)
    return {"status": "queued"}


async def _handle_imaging_order(payload: dict):
    detail = payload.get("order_detail") or {}
    if isinstance(detail, str):
        import json
        try:   detail = json.loads(detail)
        except Exception: detail = {}

    fhir_sr = to_fhir_service_request(payload, payload.get("patient_id", ""))
    await _push_to_fhir(fhir_sr)

    ris_payload = {
        "modality":             detail.get("modality", "CR"),
        "bodypart":             detail.get("bodypart"),
        "priority":             payload.get("priority", "ROUTINE"),
        "requesting_physician": payload.get("requesting_physician"),
        "clinical_info":        detail.get("clinical_info"),
        "scheduled_date":       detail.get("scheduled_date"),
        "patient_id":            1,   # TODO: resolve via MRN lookup
    }
    resp_data = {}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{RIS_URL}/orders", json=ris_payload)
            resp_data = r.json()
    except Exception as e:
        log.warning(f"RIS order creation failed: {e}")
        return

    accession = resp_data.get("accession_number")
    if accession and payload.get("id"):
        # FIX: use PATCH not POST — EHR order update endpoint is PATCH
        await _patch(f"{EHR_URL}/orders/{payload['id']}",
                     {"ehr_order_id": accession, "status": "SENT"})


@router.post("/lab-order")
async def on_lab_order(payload: dict, bg: BackgroundTasks):
    """EHR lab order → create LIS specimen + order. Push FHIR ServiceRequest."""
    bg.add_task(_handle_lab_order, payload)
    return {"status": "queued"}


async def _handle_lab_order(payload: dict):
    detail = payload.get("order_detail") or {}
    if isinstance(detail, str):
        import json
        try:   detail = json.loads(detail)
        except Exception: detail = {}

    fhir_sr = to_fhir_service_request(payload, payload.get("patient_id", ""))
    await _push_to_fhir(fhir_sr)

    # mrn is included via ORDER_SQL JOIN in ehr/routers/orders.py
    mrn = payload.get("mrn", "")
    specimen_id = None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            pts = (await c.get(f"{LIS_URL}/lab-patients?q={mrn}")).json()
            lis_patient_id = pts[0]["id"] if pts else None
            if lis_patient_id:
                spec = (await c.post(f"{LIS_URL}/specimens", json={
                    "patient_id":    lis_patient_id,
                    "specimen_type": detail.get("specimen_type", "blood"),
                    "collected_by":  payload.get("requesting_physician"),
                })).json()
                specimen_id = spec.get("id")
    except Exception as e:
        log.warning(f"LIS specimen creation failed: {e}")

    if not specimen_id:
        return

    lis_order_id = None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            order = (await c.post(f"{LIS_URL}/lab-orders", json={
                "ehr_order_id": str(payload.get("id")),
                "specimen_id":  specimen_id,
                "test_code":    detail.get("test_code", "CBC"),
                "priority":     payload.get("priority", "ROUTINE"),
                "ordered_by":   payload.get("requesting_physician"),
            })).json()
            lis_order_id = order.get("id")
    except Exception as e:
        log.warning(f"LIS order creation failed: {e}")
        return

    if lis_order_id and payload.get("id"):
        # FIX: use PATCH not POST — EHR order update endpoint is PATCH
        await _patch(f"{EHR_URL}/orders/{payload['id']}",
                     {"external_ref": f"LIS-{lis_order_id}", "status": "SENT"})


@router.post("/lab-result-final")
async def on_lab_result_final(payload: dict, bg: BackgroundTasks):
    """LIS final result → FHIR DiagnosticReport + push to EHR CDSS."""
    bg.add_task(_handle_lab_result, payload)
    return {"status": "queued"}


async def _handle_lab_result(payload: dict):
    fhir_dr = to_fhir_diagnostic_report_lab(payload)
    await _push_to_fhir(fhir_dr)
    await _post(f"{EHR_URL}/orders/from-lis-result", payload)


@router.post("/report-final")
async def on_report_final(payload: dict, bg: BackgroundTasks):
    """RIS FINAL report → FHIR DiagnosticReport (radiology)."""
    bg.add_task(_handle_report_final, payload)
    return {"status": "queued"}


async def _handle_report_final(payload: dict):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{RIS_URL}/reports/order/{payload.get('order_id')}")
            report = r.json()
    except Exception:
        report = payload
    fhir_dr = to_fhir_diagnostic_report_radiology(report)
    await _push_to_fhir(fhir_dr)


@router.post("/dicom-stored")
async def on_dicom_stored(payload: dict, bg: BackgroundTasks):
    """Orthanc stored instance → FHIR ImagingStudy."""
    bg.add_task(_handle_dicom_stored, payload)
    return {"status": "queued"}


async def _handle_dicom_stored(payload: dict):
    instance_id = payload.get("instanceId")
    if not instance_id:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            inst     = (await c.get(f"{ORTHANC_URL}/instances/{instance_id}")).json()
            series_id = inst.get("ParentSeries")
            if not series_id:
                return
            series   = (await c.get(f"{ORTHANC_URL}/series/{series_id}")).json()
            study_id = series.get("ParentStudy")
            patient_tags = {}
            if study_id:
                study = (await c.get(f"{ORTHANC_URL}/studies/{study_id}")).json()
                patient_tags = study.get("PatientMainDicomTags", {})
                series["StudyMainDicomTags"] = study.get("MainDicomTags", {})
            fhir_study = to_fhir_imaging_study(
                series, patient_tags.get("PatientID", "unknown"))
            await _push_to_fhir(fhir_study)
    except Exception as e:
        log.warning(f"DICOM stored handler failed: {e}")


@router.post("/ai-job-completed")
async def on_ai_job_completed(payload: dict, bg: BackgroundTasks):
    """AI Controller job completed → FHIR Observations."""
    bg.add_task(_handle_ai_job, payload)
    return {"status": "queued"}


async def _handle_ai_job(payload: dict):
    job_id = payload.get("job_id")
    if not job_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            job = (await c.get(f"{AI_URL}/jobs/{job_id}")).json()
    except Exception as e:
        log.warning(f"AI job fetch failed: {e}")
        return
    for obs in to_fhir_observations_from_ai(job):
        await _push_to_fhir(obs)
