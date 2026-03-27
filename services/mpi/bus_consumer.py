"""
bus_consumer — MPI subscriber for the openhis:events Redis stream.

Listens for patient.synced events published by the integration-hub and
automatically upserts a cross-reference entry linking the OpenMRS patient
UUID to the MPI master record (matched by MRN).

Consumer group: mpi
Consumer name:  mpi-1
"""
import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

from database import get_db

log = logging.getLogger("mpi.bus")

STREAM        = "openhis:events"
GROUP         = "mpi"
CONSUMER      = "mpi-1"
BLOCK_MS      = 5_000   # block up to 5 s waiting for new messages
BATCH         = 20

REDIS_URL: str = os.environ.get("REDIS_URL", "")

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


async def _handle_patient_synced(payload: dict) -> None:
    """
    Upsert a cross-reference: system=openmrs / system=openelis → master_id.

    Lookup is by MRN; if no master record exists yet the event is silently
    skipped (the MPI record will be created via the REST API when the patient
    is registered directly).
    """
    mrn    = payload.get("mrn")
    omrs_id = payload.get("omrs_id")
    oe_id   = payload.get("oe_id")

    if not mrn:
        return

    with get_db() as db:
        row = db.execute(
            "SELECT id FROM master_patients WHERE mrn = ? AND status = 'active'",
            (mrn,),
        ).fetchone()
        if not row:
            log.debug("No master patient for MRN %s — skipping crossref", mrn)
            return

        master_id = row["id"]

        for system, system_id in [("openmrs", omrs_id), ("openelis", oe_id)]:
            if not system_id:
                continue
            db.execute(
                """
                INSERT INTO cross_references (master_id, system, system_id, mrn)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(system, system_id) DO UPDATE SET mrn = excluded.mrn
                """,
                (master_id, system, system_id, mrn),
            )

    log.info("Crossref upserted for MRN %s (omrs=%s, oe=%s)", mrn, omrs_id, oe_id)


_HANDLERS = {
    "patient.synced": _handle_patient_synced,
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


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    if not REDIS_URL:
        log.info("REDIS_URL not set — bus consumer disabled")
        return

    r = _get_client()

    # Ensure stream and group exist
    try:
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            log.warning("xgroup_create: %s", e)

    log.info("MPI bus consumer started (stream=%s group=%s)", STREAM, GROUP)

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
            log.info("MPI bus consumer stopping")
            break
        except Exception as exc:
            log.error("Bus consumer error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)
