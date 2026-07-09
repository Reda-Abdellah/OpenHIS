"""
Async job runner.

Flow per job:
  Imaging:
    1. Resolve series → download all DICOM instances to /data/jobs/{id}/input/
    2. Write input.json with DICOM metadata
    3. docker run with the shared ai-jobs volume
    4. Parse /data/jobs/{id}/output/result.json
    5. Register output artifacts in DB
    6. Apply auto-saveback if rule says so

  Clinical (lab_result, emr_event, …):
    1. Write payload-only input.json (optionally enriched with FHIR resource)
    2. docker run (same as imaging)
    3. Parse result.json (same as imaging)
    — No DICOM download, no saveback
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

import docker
import docker.errors

import orthanc_client as oc
from database import get_db, rows_to_list

log = logging.getLogger("runner")

JOBS_DATA_DIR    = os.environ.get("JOBS_DATA_DIR", "/data/jobs")
JOBS_VOLUME      = os.environ.get("JOBS_VOLUME_NAME", "openhis_ai-jobs")
DOCKER_NETWORK   = os.environ.get("DOCKER_NETWORK", "openhis_openhis-net")
CONTAINER_TIMEOUT = int(os.environ.get("CONTAINER_TIMEOUT_S", "300"))
OPENELIS_URL     = os.environ.get("OPENELIS_URL", "")

# Container hardening (T-02). Only images on the allowlist may be spawned,
# and every pipeline container runs with hard resource limits.
# The default covers all four seeded pipelines (database.py): the two imaging
# POCs plus the bus-triggered clinical POCs (lab-risk, emr-alert).
POC_ALLOWED_IMAGES: frozenset[str] = frozenset(
    img.strip()
    for img in os.environ.get(
        "POC_ALLOWED_IMAGES",
        "openhis/poc-xray:latest,openhis/poc-ct:latest,"
        "openhis/poc-lab-risk:latest,openhis/poc-emr-alert:latest",
    ).split(",")
    if img.strip()
)
POC_MEM_LIMIT    = os.environ.get("POC_MEM_LIMIT", "2g")
POC_PIDS_LIMIT   = int(os.environ.get("POC_PIDS_LIMIT", "256"))
POC_CPU_LIMIT    = float(os.environ.get("POC_CPU_LIMIT", "2"))
CONTAINER_LOG_TAIL = 1000

# job_id becomes a path segment under JOBS_DATA_DIR — keep it filename-safe.
_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_job_id(job_id: str) -> str:
    """Reject job ids that could escape JOBS_DATA_DIR (path traversal)."""
    if not _JOB_ID_RE.fullmatch(job_id or ""):
        raise RuntimeError(f"invalid job_id: {job_id!r}")
    return job_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── helpers ───────────────────────────────────────────────────────────────────

def _set_job_status(job_id: str, status: str, **kwargs):
    sets = ["status=?"] + [f"{k}=?" for k in kwargs]
    vals = [status] + list(kwargs.values()) + [job_id]
    with get_db() as db:
        db.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=?", vals)


def _register_artifact(job_id, direction, atype, filename, rel_path,
                       size_bytes=None, sop_class=None, instance_uid=None):
    with get_db() as db:
        db.execute(
            """INSERT INTO artifacts
               (job_id,direction,artifact_type,filename,rel_path,size_bytes,
                dicom_sop_class,dicom_instance_uid)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, direction, atype, filename, rel_path,
             size_bytes, sop_class, instance_uid),
        )


# ── core runner ───────────────────────────────────────────────────────────────

async def run_job(job_id: str):
    """Entry point – called from background task or bus consumer."""
    loop = asyncio.get_event_loop()

    with get_db() as db:
        row = db.execute(
            "SELECT j.*, p.source_type as pipeline_source_type "
            "FROM jobs j JOIN pipelines p ON p.id=j.pipeline_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not row:
            log.error("run_job called for unknown job %s", job_id)
            return
        job = dict(row)

    source_type = job.get("source_type") or job.get("pipeline_source_type") or "imaging"

    try:
        if source_type == "imaging":
            await _prepare_input(job_id)
        else:
            await _prepare_clinical_input(job_id)

        _set_job_status(job_id, "RUNNING", started_at=_now_iso(), container_logs="")
        container_id, logs, exit_code = await loop.run_in_executor(
            None, _run_container_sync, job_id
        )
        if exit_code != 0:
            raise RuntimeError(f"Container exited {exit_code}. Logs:\n{logs[-2000:]}")
        await _process_output(job_id)
        duration = _compute_duration(job_id)
        _set_job_status(
            job_id, "COMPLETED",
            finished_at=_now_iso(),
            duration_ms=duration,
            container_id=container_id,
            container_logs=logs[-4000:],
        )
        log.info("Job %s completed in %s ms", job_id, duration)

        if source_type == "imaging":
            await _maybe_auto_saveback(job_id)

        _bridge = os.environ.get("FHIR_BRIDGE_URL", "")
        if _bridge:
            try:
                async with httpx.AsyncClient(timeout=3) as _c:
                    await _c.post(f"{_bridge}/api/events/ai-job-completed",
                                  json={"job_id": job_id})
            except Exception:
                pass
    except Exception as exc:
        log.exception("Job %s failed: %s", job_id, exc)
        _set_job_status(job_id, "FAILED", finished_at=_now_iso(), error=str(exc)[:1000])


async def _prepare_input(job_id: str):
    """Imaging pipeline: download DICOM instances from Orthanc."""
    _validate_job_id(job_id)
    with get_db() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    job_dir   = Path(JOBS_DATA_DIR) / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    orthanc_series_id = job.get("orthanc_series_id")
    if not orthanc_series_id:
        raise RuntimeError("No orthanc_series_id on job – cannot fetch DICOM files")

    series_meta  = await oc.get_series_metadata(orthanc_series_id)
    instance_ids = series_meta.get("Instances", [])
    if not instance_ids:
        raise RuntimeError(f"Series {orthanc_series_id} has no instances in Orthanc")

    log.info("Downloading %d instances for job %s", len(instance_ids), job_id)
    for idx, iid in enumerate(instance_ids):
        dcm_bytes = await oc.get_instance_file(iid)
        out_path  = input_dir / f"{idx+1:04d}_{iid[:8]}.dcm"
        out_path.write_bytes(dcm_bytes)
        _register_artifact(
            job_id, "input", "dicom",
            out_path.name, f"{job_id}/input/{out_path.name}",
            size_bytes=len(dcm_bytes),
        )

    with get_db() as db:
        pipeline = dict(
            db.execute("SELECT * FROM pipelines WHERE id=?", (job["pipeline_id"],)).fetchone()
        )

    input_meta = {
        "job_id":            job_id,
        "pipeline_id":       job["pipeline_id"],
        "source_type":       "imaging",
        "series_uid":        job["series_uid"],
        "study_uid":         job["study_uid"],
        "patient_name":      job.get("patient_name", ""),
        "patient_id":        job.get("patient_id", ""),
        "modality":          job.get("modality", ""),
        "body_part":         job.get("body_part", ""),
        "accession_number":  job.get("accession_number", ""),
        "instance_count":    len(instance_ids),
        "pipeline_config":   json.loads(pipeline.get("config_json") or "{}"),
    }
    (input_dir / "input.json").write_text(json.dumps(input_meta, indent=2))


async def _prepare_clinical_input(job_id: str):
    """
    Clinical pipeline (lab_result, emr_event, …): write a payload-only input.json.

    For lab_result pipelines, optionally enriches the payload with the full FHIR
    DiagnosticReport fetched from OpenELIS. Falls back gracefully if unreachable.
    """
    _validate_job_id(job_id)
    with get_db() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        pipeline = dict(
            db.execute("SELECT * FROM pipelines WHERE id=?", (job["pipeline_id"],)).fetchone()
        )

    job_dir   = Path(JOBS_DATA_DIR) / job_id
    input_dir  = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_type = job.get("source_type", "lab_result")
    event_payload = json.loads(job.get("event_payload") or "{}")

    # Optional FHIR enrichment for lab_result pipelines
    if source_type == "lab_result" and OPENELIS_URL:
        oe_id = event_payload.get("oe_id") or job.get("event_source_id", "")
        if oe_id:
            fhir_resource = await _fetch_fhir_resource(
                f"{OPENELIS_URL}/fhir/R4/DiagnosticReport/{oe_id}"
            )
            if fhir_resource:
                event_payload["fhir_resource"] = fhir_resource

    input_meta = {
        "job_id":            job_id,
        "pipeline_id":       job["pipeline_id"],
        "source_type":       source_type,
        "event_source_id":   job.get("event_source_id", ""),
        "patient_id":        job.get("patient_id", ""),
        "pipeline_config":   json.loads(pipeline.get("config_json") or "{}"),
        "payload":           event_payload,
    }
    payload_str = json.dumps(input_meta, indent=2)
    (input_dir / "input.json").write_text(payload_str)
    _register_artifact(
        job_id, "input", "json_payload",
        "input.json", f"{job_id}/input/input.json",
        size_bytes=len(payload_str),
    )


async def _fetch_fhir_resource(url: str) -> dict:
    """Fetch a FHIR resource by URL. Returns empty dict on any error."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


def _run_container_sync(job_id: str) -> tuple[str, str, int]:
    _validate_job_id(job_id)
    with get_db() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        img = dict(db.execute(
            "SELECT docker_image FROM pipelines WHERE id=?", (job["pipeline_id"],)
        ).fetchone())["docker_image"]

    if img not in POC_ALLOWED_IMAGES:
        raise RuntimeError(
            f"image not in allowlist: {img!r} (set POC_ALLOWED_IMAGES to extend)"
        )

    client    = docker.from_env()
    container = client.containers.run(
        image=img,
        environment={"JOB_ID": job_id},
        volumes={JOBS_VOLUME: {"bind": "/data/jobs", "mode": "rw"}},
        network=DOCKER_NETWORK,
        detach=True,
        remove=False,
        mem_limit=POC_MEM_LIMIT,
        memswap_limit=POC_MEM_LIMIT,
        pids_limit=POC_PIDS_LIMIT,
        nano_cpus=int(POC_CPU_LIMIT * 1e9),
        security_opt=["no-new-privileges"],
        cap_drop=["ALL"],
    )
    try:
        result = container.wait(timeout=CONTAINER_TIMEOUT)
        logs   = container.logs(tail=CONTAINER_LOG_TAIL).decode("utf-8", errors="replace")
        return container.id, logs, result["StatusCode"]
    finally:
        try:
            container.remove(force=True)
        except docker.errors.NotFound:
            pass


async def _process_output(job_id: str):
    output_dir  = Path(JOBS_DATA_DIR) / job_id / "output"
    result_file = output_dir / "result.json"
    if not result_file.exists():
        raise RuntimeError("Pipeline did not produce output/result.json")

    result = json.loads(result_file.read_text())
    _register_artifact(
        job_id, "output", "json_report",
        "result.json", f"{job_id}/output/result.json",
        size_bytes=result_file.stat().st_size,
    )

    for dcm_file in sorted(output_dir.glob("*.dcm")):
        import pydicom, io as _io
        try:
            ds  = pydicom.dcmread(_io.BytesIO(dcm_file.read_bytes()))
            sop = str(ds.get("SOPClassUID", ""))
            uid = str(ds.get("SOPInstanceUID", ""))
            atype = _dicom_artifact_type(sop)
        except Exception:
            sop, uid, atype = "", "", "dicom"
        _register_artifact(
            job_id, "output", atype,
            dcm_file.name, f"{job_id}/output/{dcm_file.name}",
            size_bytes=dcm_file.stat().st_size,
            sop_class=sop, instance_uid=uid,
        )

    summary = {
        "normal":          result.get("normal", True),
        "critical":        result.get("critical", False),
        "risk_score":      result.get("risk_score"),
        "findings_count":  len(result.get("findings", [])),
        "impression":      result.get("impression", ""),
        "follow_up":       result.get("follow_up_recommended", False),
        "output_files":    result.get("output_files", []),
    }
    with get_db() as db:
        db.execute("UPDATE jobs SET result_summary=? WHERE id=?",
                   (json.dumps(summary), job_id))


def _dicom_artifact_type(sop_class: str) -> str:
    SC  = "1.2.840.10008.5.1.4.1.1.7"
    SEG = "1.2.840.10008.5.1.4.1.1.66.4"
    SR  = "1.2.840.10008.5.1.4.1.1.88"
    if sop_class.startswith(SR): return "structured_report"
    if sop_class == SEG:          return "segmentation"
    if sop_class == SC:           return "secondary_capture"
    return "dicom"


def _compute_duration(job_id: str) -> int | None:
    with get_db() as db:
        row = db.execute(
            "SELECT started_at, finished_at FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    if not row or not row["started_at"] or not row["finished_at"]:
        return None
    t0 = datetime.fromisoformat(row["started_at"])
    t1 = datetime.fromisoformat(row["finished_at"])
    return int((t1 - t0).total_seconds() * 1000)


async def _maybe_auto_saveback(job_id: str):
    with get_db() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    rule_id = job.get("rule_id")
    if not rule_id:
        return
    with get_db() as db:
        rule = db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    if not rule or not rule["auto_saveback"]:
        return
    saveback_types = json.loads(rule["saveback_types"] or "[]")
    with get_db() as db:
        artifacts = rows_to_list(db.execute(
            "SELECT * FROM artifacts WHERE job_id=? AND direction='output'", (job_id,)
        ).fetchall())

    for art in artifacts:
        if art["artifact_type"] in saveback_types or "all" in saveback_types:
            await saveback_artifact(job_id, art["id"], trigger_type="AUTO")


async def saveback_artifact(job_id: str, artifact_id: int, trigger_type: str = "MANUAL"):
    """Upload one output artifact to Orthanc and record the event."""
    with get_db() as db:
        art = dict(db.execute(
            "SELECT * FROM artifacts WHERE id=? AND job_id=?", (artifact_id, job_id)
        ).fetchone())

    file_path = Path(JOBS_DATA_DIR) / art["rel_path"]
    if not file_path.exists():
        raise FileNotFoundError(f"Artifact file not found: {file_path}")
    if file_path.suffix.lower() != ".dcm":
        raise ValueError("Only DICOM artifacts can be saved back to PACS")

    dicom_bytes = file_path.read_bytes()
    try:
        resp       = await oc.upload_dicom(dicom_bytes)
        orthanc_id = resp.get("ID", "")
        with get_db() as db:
            db.execute("UPDATE artifacts SET orthanc_instance_id=? WHERE id=?",
                       (orthanc_id, artifact_id))
            db.execute(
                "INSERT INTO saveback_events"
                " (job_id,artifact_id,orthanc_instance_id,status,trigger_type,completed_at)"
                " VALUES (?,?,?,'SUCCESS',?,?)",
                (job_id, artifact_id, orthanc_id, trigger_type, _now_iso()),
            )
        log.info("Saveback OK: artifact %s → Orthanc %s", artifact_id, orthanc_id)
        return orthanc_id
    except Exception as exc:
        with get_db() as db:
            db.execute(
                "INSERT INTO saveback_events"
                " (job_id,artifact_id,status,trigger_type,error,completed_at)"
                " VALUES (?,?,'FAILED',?,?,?)",
                (job_id, artifact_id, trigger_type, str(exc)[:500], _now_iso()),
            )
        raise
