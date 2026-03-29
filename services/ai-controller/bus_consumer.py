"""
bus_consumer — AI Controller subscriber for the openhis:events Redis stream.

Listens for clinical data events (lab_result.ready, patient.synced) and
automatically triggers matching AI pipeline jobs when auto-trigger rules
are configured for those source types.

Consumer group: ai-controller
Consumer name:  ai-controller-1
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from database import get_db, rows_to_list

log = logging.getLogger("ai-controller.bus")

STREAM   = "openhis:events"
GROUP    = "ai-controller"
CONSUMER = "ai-controller-1"
BLOCK_MS = 5_000   # block up to 5 s waiting for new messages
BATCH    = 20

REDIS_URL: str = os.environ.get("REDIS_URL", "")

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


# ── rule matching ─────────────────────────────────────────────────────────────

def _matches_clinical_rule(rule: dict, payload: dict) -> bool:
    """
    Evaluate a rule's trigger_filter against the event payload.
    All key=value pairs in trigger_filter must be present and equal in payload.
    An empty filter ({}) matches every event of the pipeline's source_type.
    """
    raw = rule.get("trigger_filter") or "{}"
    try:
        filter_dict = json.loads(raw)
    except Exception:
        return False
    return all(payload.get(k) == v for k, v in filter_dict.items())


def _check_existing_clinical_job(event_source_id: str, pipeline_id: str) -> bool:
    """
    Return True if a non-failed job already exists for this event + pipeline.
    FAILED jobs are excluded so a retry can be triggered on a new event.
    """
    with get_db() as db:
        row = db.execute(
            """SELECT 1 FROM jobs
               WHERE event_source_id=? AND pipeline_id=?
               AND status IN ('PENDING','RUNNING','COMPLETED')""",
            (event_source_id, pipeline_id),
        ).fetchone()
    return row is not None


async def _create_and_enqueue_job(
    pipeline_id: str,
    rule_id: int,
    source_type: str,
    event_source_id: str,
    patient_id: str,
    event_payload: dict,
) -> str:
    """Insert a PENDING job row and schedule run_job() as a background task."""
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs "
            "(id,pipeline_id,rule_id,series_uid,study_uid,"
            " source_type,event_source_id,event_payload,"
            " patient_id,status,trigger_type,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, pipeline_id, rule_id,
                "", "",   # series_uid / study_uid empty for non-imaging
                source_type,
                event_source_id,
                json.dumps(event_payload),
                patient_id,
                "PENDING", "AUTO", now,
            ),
        )
    asyncio.create_task(_run_job_task(job_id))
    log.info("Enqueued %s job %s (event_source=%s)", source_type, job_id, event_source_id)
    return job_id


async def _run_job_task(job_id: str) -> None:
    from runner import run_job
    await run_job(job_id)


# ── event handlers ────────────────────────────────────────────────────────────

async def _handle_lab_result_ready(payload: dict) -> None:
    """
    Fired by integration-hub when an OpenELIS DiagnosticReport reaches status=final.
    payload: {"oe_id": str, "subject": str}
    """
    oe_id = payload.get("oe_id")
    if not oe_id:
        return

    with get_db() as db:
        rules = rows_to_list(db.execute("""
            SELECT r.*, p.source_type as pipeline_source_type
            FROM rules r
            JOIN pipelines p ON p.id = r.pipeline_id
            WHERE r.auto_trigger = 1
              AND r.enabled = 1
              AND p.enabled = 1
              AND p.source_type = 'lab_result'
            ORDER BY r.priority DESC
        """).fetchall())

    for rule in rules:
        if not _matches_clinical_rule(rule, payload):
            continue
        if _check_existing_clinical_job(oe_id, rule["pipeline_id"]):
            log.debug("Dedup: job already exists for oe_id=%s pipeline=%s", oe_id, rule["pipeline_id"])
            continue
        await _create_and_enqueue_job(
            pipeline_id=rule["pipeline_id"],
            rule_id=rule["id"],
            source_type="lab_result",
            event_source_id=oe_id,
            patient_id=payload.get("subject", ""),
            event_payload=payload,
        )


async def _handle_dicom_stored(payload: dict) -> None:
    """
    Fired by integration-hub when an Orthanc DICOM instance is stored.
    payload: {"study_uid": str, "patient_id": str, "modality": str, "ts": str}
    """
    study_uid  = payload.get("study_uid")
    patient_id = payload.get("patient_id", "")
    modality   = payload.get("modality", "").upper()

    if not study_uid:
        return

    with get_db() as db:
        rules = rows_to_list(db.execute("""
            SELECT r.*, p.source_type as pipeline_source_type
            FROM rules r
            JOIN pipelines p ON p.id = r.pipeline_id
            WHERE r.auto_trigger = 1
              AND r.enabled = 1
              AND p.enabled = 1
              AND p.source_type = 'dicom'
            ORDER BY r.priority DESC
        """).fetchall())

    for rule in rules:
        rule_filter = json.loads(rule.get("trigger_filter") or "{}")
        if rule_filter.get("modality") and modality and rule_filter["modality"].upper() != modality:
            continue
        if _check_existing_clinical_job(study_uid, rule["pipeline_id"]):
            log.debug("Dedup: job already exists for study_uid=%s pipeline=%s", study_uid, rule["pipeline_id"])
            continue
        await _create_and_enqueue_job(
            pipeline_id=rule["pipeline_id"],
            rule_id=rule["id"],
            source_type="dicom",
            event_source_id=study_uid,
            patient_id=patient_id,
            event_payload=payload,
        )


async def _handle_patient_synced(payload: dict) -> None:
    """
    Fired by integration-hub when a patient is synced from OpenMRS → OpenELIS.
    payload: {"omrs_id": str, "oe_id": str, "mrn": str}
    """
    omrs_id = payload.get("omrs_id")
    if not omrs_id:
        return

    with get_db() as db:
        rules = rows_to_list(db.execute("""
            SELECT r.*, p.source_type as pipeline_source_type
            FROM rules r
            JOIN pipelines p ON p.id = r.pipeline_id
            WHERE r.auto_trigger = 1
              AND r.enabled = 1
              AND p.enabled = 1
              AND p.source_type = 'emr_event'
            ORDER BY r.priority DESC
        """).fetchall())

    for rule in rules:
        if not _matches_clinical_rule(rule, payload):
            continue
        if _check_existing_clinical_job(omrs_id, rule["pipeline_id"]):
            log.debug("Dedup: job already exists for omrs_id=%s pipeline=%s", omrs_id, rule["pipeline_id"])
            continue
        await _create_and_enqueue_job(
            pipeline_id=rule["pipeline_id"],
            rule_id=rule["id"],
            source_type="emr_event",
            event_source_id=omrs_id,
            patient_id=omrs_id,
            event_payload=payload,
        )


_HANDLERS = {
    "lab_result.ready": _handle_lab_result_ready,
    "patient.synced":   _handle_patient_synced,
    "dicom.stored":     _handle_dicom_stored,
}


async def _process_message(entry_id: str, fields: dict) -> None:
    event_type = fields.get("type", "")
    handler = _HANDLERS.get(event_type)
    if handler is None:
        return
    try:
        payload = json.loads(fields.get("payload", "{}"))
        await handler(payload)
    except Exception as exc:
        log.error("Error handling %s (%s): %s", event_type, entry_id, exc)


# ── main loop ─────────────────────────────────────────────────────────────────

async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    if not REDIS_URL:
        log.info("REDIS_URL not set — AI controller bus consumer disabled")
        return

    r = _get_client()

    try:
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            log.warning("xgroup_create: %s", e)

    log.info("AI controller bus consumer started (stream=%s group=%s)", STREAM, GROUP)

    while True:
        try:
            results = await r.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={STREAM: ">"},
                count=BATCH,
                block=BLOCK_MS,
            )
            if not results:
                continue

            for _stream, messages in results:
                for entry_id, fields in messages:
                    await _process_message(entry_id, fields)
                    await r.xack(STREAM, GROUP, entry_id)

        except asyncio.CancelledError:
            log.info("AI controller bus consumer stopping")
            break
        except Exception as exc:
            log.error("Bus consumer error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)
