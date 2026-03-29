"""
Legacy event handlers — replaces fhir-bridge for the three event types
that are still emitted by services that survive Phase 5 cutover:

  POST /api/events/report-final      ← RIS (radiology report)
  POST /api/events/dicom-stored      ← Orthanc (DICOM instance stored)
  POST /api/events/ai-job-completed  ← AI controller (job result)

Each handler translates the payload to FHIR and pushes it to OpenMRS FHIR R4.
Patient resolution is best-effort: we search OpenMRS by PatientID / MRN field
from the source payload and fall back to a placeholder reference if not found.
"""
import logging
import httpx
from fastapi import APIRouter, BackgroundTasks
from app.config import OPENMRS_URL, OPENMRS_USER, OPENMRS_PASS
from app.translators.diagnostic_report import to_fhir_diagnostic_report_radiology
from app.translators.imaging_study import to_fhir_imaging_study
from app.translators.observation import to_fhir_observations_from_ai
from app import bus
from app.db import audit
from app.utils.retry import with_retry

log = logging.getLogger("hub.events")

router = APIRouter(prefix="/api/events", tags=["events"])

_FHIR   = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
_AUTH   = (OPENMRS_USER, OPENMRS_PASS)
_HDR    = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
_ORTHANC = "http://orthanc:8042"


# ── helpers ───────────────────────────────────────────────────────────────────

async def _push(resource: dict) -> None:
    """POST a FHIR resource to OpenMRS FHIR R4. Errors are logged, not raised."""
    rtype = resource.get("resourceType", "Resource")
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=15) as c:
            r = await c.post(f"{_FHIR}/{rtype}", json=resource, headers=_HDR)
            if r.status_code in (200, 201):
                log.info(f"FHIR {rtype} → OpenMRS HTTP {r.status_code}")
            else:
                log.warning(f"FHIR {rtype} → OpenMRS HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"_push {rtype} failed: {e}")


async def _resolve_patient_uuid(mrn_or_id: str) -> str:
    """Search OpenMRS for a patient by identifier value. Returns UUID or placeholder."""
    if not mrn_or_id:
        return "unknown"
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=8) as c:
            r = await c.get(f"{_FHIR}/Patient",
                            params={"identifier": mrn_or_id},
                            headers=_HDR)
            entries = r.json().get("entry", []) if r.status_code == 200 else []
            if entries:
                return entries[0]["resource"]["id"]
    except Exception:
        pass
    return f"unknown-{mrn_or_id}"


# ── event handlers ────────────────────────────────────────────────────────────

@router.post("/report-final")
async def on_report_final(payload: dict, bg: BackgroundTasks):
    """RIS FINAL radiology report → FHIR DiagnosticReport → OpenMRS."""
    bg.add_task(_handle_report_final, payload)
    return {"status": "queued"}


@with_retry(max_attempts=3, base_delay=1.0)
async def _push_with_retry(resource: dict) -> None:
    await _push(resource)


async def _handle_report_final(payload: dict):
    await audit.log_event("webhook_received", "DiagnosticReport", "", "ris→hub", "ok")
    # Fetch full report from RIS if only order_id is provided
    report = payload
    if payload.get("order_id") and not payload.get("impression"):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"http://ris:8002/api/reports/order/{payload['order_id']}")
                if r.status_code == 200:
                    report = r.json()
        except Exception:
            pass

    try:
        patient_ref = await _resolve_patient_uuid(
            str(report.get("ehr_patient_id", "") or report.get("patient_id", "")))
        dr = to_fhir_diagnostic_report_radiology(report)
        dr["subject"] = {"reference": f"Patient/{patient_ref}"}
        await _push_with_retry(dr)
        await audit.log_event("fhir_pushed", "DiagnosticReport", report.get("order_id", ""), "hub→omrs", "ok")
        await bus.publish("radiology.report.ready", {
            "order_id": report.get("order_id"),
            "patient_id": patient_ref,
        })
    except Exception as exc:
        await audit.log_event("fhir_push_failed", "DiagnosticReport", report.get("order_id", ""), "hub→omrs", "failed", str(exc))


@router.post("/dicom-stored")
async def on_dicom_stored(payload: dict, bg: BackgroundTasks):
    """Orthanc stored DICOM instance → FHIR ImagingStudy → OpenMRS."""
    bg.add_task(_handle_dicom_stored, payload)
    return {"status": "queued"}


async def _handle_dicom_stored(payload: dict):
    await audit.log_event("webhook_received", "ImagingStudy", "", "orthanc→hub", "ok")
    instance_id = payload.get("instanceId")
    if not instance_id:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            inst     = (await c.get(f"{_ORTHANC}/instances/{instance_id}")).json()
            series_id = inst.get("ParentSeries")
            if not series_id:
                return
            series   = (await c.get(f"{_ORTHANC}/series/{series_id}")).json()
            study_id = series.get("ParentStudy")
            patient_tags: dict = {}
            if study_id:
                study = (await c.get(f"{_ORTHANC}/studies/{study_id}")).json()
                patient_tags = study.get("PatientMainDicomTags", {})
                series["StudyMainDicomTags"] = study.get("MainDicomTags", {})
    except Exception as e:
        log.warning(f"DICOM fetch failed: {e}")
        await audit.log_event("dicom_fetch_failed", "ImagingStudy", instance_id, "orthanc→hub", "failed", str(e))
        return

    try:
        # PatientID in DICOM is typically the MRN
        mrn = patient_tags.get("PatientID", "")
        patient_uuid = await _resolve_patient_uuid(mrn)
        fhir_study = to_fhir_imaging_study(series, patient_uuid)
        await _push_with_retry(fhir_study)
        study_uid = series.get("MainDicomTags", {}).get("StudyInstanceUID", "")
        await audit.log_event("fhir_pushed", "ImagingStudy", study_uid, "hub→omrs", "ok")
        await bus.publish("dicom.stored", {
            "study_uid": study_uid,
            "patient_id": mrn,
            "modality": series.get("MainDicomTags", {}).get("Modality", ""),
        })
    except Exception as exc:
        await audit.log_event("fhir_push_failed", "ImagingStudy", instance_id, "hub→omrs", "failed", str(exc))


@router.post("/ai-job-completed")
async def on_ai_job_completed(payload: dict, bg: BackgroundTasks):
    """AI controller job completed → FHIR Observations → OpenMRS."""
    bg.add_task(_handle_ai_job, payload)
    return {"status": "queued"}


async def _handle_ai_job(payload: dict):
    await audit.log_event("webhook_received", "Observation", "", "ai-controller→hub", "ok")
    job_id = payload.get("job_id")
    if not job_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            job = (await c.get(f"http://ai-controller:8000/api/jobs/{job_id}")).json()
    except Exception as e:
        log.warning(f"AI job fetch failed: {e}")
        await audit.log_event("ai_fetch_failed", "Observation", job_id, "ai-controller→hub", "failed", str(e))
        return

    try:
        observations = to_fhir_observations_from_ai(job)
        for obs in observations:
            await _push_with_retry(obs)
        await audit.log_event("fhir_pushed", "Observation", job_id, "hub→omrs", "ok")
        await bus.publish("ai.result.ready", {
            "job_id": job_id,
            "pipeline_id": job.get("pipeline_id"),
            "patient_id": job.get("patient_id"),
        })
    except Exception as exc:
        await audit.log_event("fhir_push_failed", "Observation", job_id, "hub→omrs", "failed", str(exc))
