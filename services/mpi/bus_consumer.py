"""
bus_consumer — MPI subscriber for the openhis:events Redis stream.

Listens for patient.registered events published by the integration-hub,
upserts the master patient record, registers OpenMRS and OpenELIS crossrefs,
then publishes patient.synced so downstream consumers (analytics, hl7) have
a stable master_id to reference.

Consumer group: mpi
Consumer name:  mpi-1
"""
import datetime
import logging
import os

import redis.asyncio as aioredis

from database import get_db, row_to_dict, new_id
from openhis_sdk.bus import BusConsumer, publish_event

log = logging.getLogger("mpi.bus")

REDIS_URL: str = os.environ.get("REDIS_URL", "")

_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def _handle_patient_registered(payload: dict) -> None:
    """
    Upsert master patient from a patient.registered event, register crossrefs
    for every known system ID, then publish patient.synced with the master_id.

    Expected payload keys:
        mrn, omrs_id, oe_id (optional), firstname, lastname, birthdate, sex
    """
    mrn     = (payload.get("mrn") or "").strip()
    omrs_id = payload.get("omrs_id")
    oe_id   = payload.get("oe_id")

    if not mrn:
        log.debug("patient.registered skipped — no MRN in payload")
        return

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    with get_db() as db:
        # ── 1. Look up existing master by MRN ────────────────────────────────
        row = db.execute(
            "SELECT * FROM master_patients WHERE mrn = ? AND status = 'active'",
            (mrn,),
        ).fetchone()

        if row:
            master = dict(row)
            # Non-destructive demographic update: fill gaps only
            fields = {
                "firstname": payload.get("firstname"),
                "lastname":  payload.get("lastname"),
                "birthdate": payload.get("birthdate"),
                "sex":       payload.get("sex"),
            }
            updates = {k: v for k, v in fields.items() if v and not master.get(k)}
            if updates:
                updates["updatedat"] = now
                sets = ", ".join(f"{k}=?" for k in updates)
                db.execute(
                    f"UPDATE master_patients SET {sets} WHERE id=?",
                    (*updates.values(), master["id"])
                )
        else:
            # ── 2. Create new master record ───────────────────────────────────
            pid = new_id()
            db.execute(
                "INSERT INTO master_patients"
                "(id,mrn,firstname,lastname,birthdate,sex,createdat,updatedat) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (pid, mrn,
                 payload.get("firstname") or "",
                 payload.get("lastname") or "",
                 payload.get("birthdate"),
                 payload.get("sex"),
                 now, now)
            )
            db.execute(
                "INSERT INTO audit_log(master_id,action,details) VALUES(?,?,?)",
                (pid, "created-from-bus", f"MRN={mrn} omrs_id={omrs_id}")
            )
            master = row_to_dict(
                db.execute("SELECT * FROM master_patients WHERE id=?", (pid,)).fetchone()
            )

        master_id = master["id"]

        # ── 3. Upsert crossrefs for every known system ID ────────────────────
        for system, system_id in [("openmrs", omrs_id), ("openelis", oe_id)]:
            if not system_id:
                continue
            db.execute(
                "INSERT INTO cross_references (master_id, system, system_id, mrn)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(system, system_id) DO UPDATE SET mrn = excluded.mrn",
                (master_id, system, system_id, mrn),
            )

    log.info(
        "MPI upsert complete — MRN=%s master_id=%s omrs=%s oe=%s",
        mrn, master_id, omrs_id, oe_id,
    )

    # ── 4. Publish patient.synced — MPI is the canonical producer of this event
    await publish_event(_get_redis(), "patient.synced", {
        "master_id": master_id,
        "mrn":       mrn,
        "omrs_id":   omrs_id,
        "oe_id":     oe_id,
    })


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    consumer = BusConsumer(
        redis_url=REDIS_URL,
        group="mpi",
        consumer="mpi-1",
        handlers={"patient.registered": _handle_patient_registered},
    )
    await consumer.run()
