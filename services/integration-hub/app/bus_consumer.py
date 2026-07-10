"""Hub bus consumer — closes the DEF-010 loop.

Subscribes to ``patient.synced``. When the event originates from the MPI
REST plane (payload ``source == "mpi"``), the patient exists only in the
MPI: the OpenMRS poll loop never sees it, so it would never reach
OpenELIS. This consumer resolves the master record + cross-references
from the MPI and upserts a FHIR Patient into OpenELIS through the
existing adapter, with an audit row per attempt.

Events published by the hub's own poll loop carry ``source ==
"integration-hub"`` (or no source) and are ignored — the poll path
already synced those patients.

Consumer group: integration-hub
Consumer name:  hub-1
"""
import logging
import os

import httpx
import redis.asyncio as aioredis

from app.db import audit
from app.services import openelis
from app.token import get_service_token
from openhis_sdk.bus import BusConsumer

log = logging.getLogger("hub.bus_consumer")

GROUP    = "integration-hub"
CONSUMER = "hub-1"

REDIS_URL = os.environ.get("REDIS_URL", "")
MPI_URL   = os.environ.get("MPI_URL", "http://mpi:8007")

# master_id → oe_id mapping. OpenELIS re-keys identifiers in its FHIR
# projection (pat_guid/pat_uuid), so the adapter's search-by-external-
# identifier can never find a previously pushed patient — without this
# map every re-emitted patient.synced would create a duplicate.
_MAP_KEY = "openhis:hub:mpi_oe_map"
_map_client: aioredis.Redis | None = None


def _get_map_client() -> aioredis.Redis | None:
    global _map_client
    if not REDIS_URL:
        return None
    if _map_client is None:
        _map_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _map_client


async def _get_mapped_oe_id(master_id: str) -> str | None:
    """Fail-soft read — a Redis hiccup must not block the sync."""
    client = _get_map_client()
    if client is None:
        return None
    try:
        return await client.hget(_MAP_KEY, master_id)
    except Exception as exc:
        log.warning("mpi→oe map read failed: %s", exc)
        return None


async def _store_mapping(master_id: str, oe_id: str) -> None:
    client = _get_map_client()
    if client is None:
        return
    try:
        await client.hset(_MAP_KEY, master_id, oe_id)
    except Exception as exc:
        log.warning("mpi→oe map write failed: %s", exc)

_SEX_TO_FHIR = {"male": "male", "female": "female", "m": "male", "f": "female"}
MRN_SYSTEM = "urn:openhis:mrn"


async def _mpi_get(path: str) -> dict | list | None:
    """Authenticated MPI read (hub SA carries internal-sync). None on failure."""
    try:
        token = await get_service_token()
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{MPI_URL}{path}",
                            headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                log.warning("MPI GET %s returned %s", path, r.status_code)
                return None
            return r.json()
    except httpx.HTTPError as exc:
        log.warning("MPI GET %s failed: %s", path, exc)
        return None


def _to_fhir_patient(master: dict, xrefs: list[dict]) -> dict:
    """Map an MPI master record + cross-references to a FHIR R4 Patient."""
    identifiers = [{"system": MRN_SYSTEM, "value": master.get("mrn", "")}]
    for x in xrefs:
        if x.get("system_id"):
            identifiers.append({
                "system": f"urn:openhis:{x.get('system', 'unknown')}",
                "value":  x["system_id"],
            })
    patient: dict = {
        "resourceType": "Patient",
        "identifier":   identifiers,
        "name": [{
            "family": master.get("lastname", ""),
            "given":  [master.get("firstname", "")],
        }],
        "active": master.get("status", "active") == "active",
    }
    sex = _SEX_TO_FHIR.get((master.get("sex") or "").lower())
    if sex:
        patient["gender"] = sex
    if master.get("birthdate"):
        patient["birthDate"] = master["birthdate"]
    return patient


async def _handle_patient_synced(payload: dict) -> None:
    """MPI-originated patient.synced → upsert the patient into OpenELIS."""
    if payload.get("source") != "mpi":
        return  # poll-loop patients are already synced by the worker

    master_id = payload.get("master_id")
    if not master_id:
        log.warning("patient.synced from mpi without master_id: %s", payload)
        return

    already = await _get_mapped_oe_id(master_id)
    if already:
        # Re-emitted event (e.g. after a cross-reference create) for a
        # patient OE already holds. OE's re-keyed identifiers make a
        # targeted update impossible without its own id semantics —
        # record and move on rather than create a duplicate.
        await audit.log_event(
            "patient_synced", "Patient", master_id, "mpi→oe", "ok",
            f"already synced as oe_id={already} — skipped",
        )
        return

    master = await _mpi_get(f"/api/patients/{master_id}")
    if not isinstance(master, dict):
        # Raise so the SDK keeps the entry pending — the MPI may just be
        # restarting; after max_delivery attempts it lands on the DLQ.
        raise RuntimeError(f"MPI record {master_id} unavailable")

    xrefs = await _mpi_get(f"/api/crossref?master_id={master_id}")
    if not isinstance(xrefs, list):
        xrefs = []

    oe_id = await openelis.upsert_patient(_to_fhir_patient(master, xrefs))
    if oe_id:
        await _store_mapping(master_id, oe_id)
        await audit.log_event(
            "patient_synced", "Patient", master_id, "mpi→oe", "ok",
            f"oe_id={oe_id} xrefs={len(xrefs)}",
        )
        log.info("MPI patient %s upserted into OpenELIS as %s", master_id, oe_id)
    else:
        await audit.log_event(
            "patient_sync_failed", "Patient", master_id, "mpi→oe", "failed",
        )
        raise RuntimeError(f"OpenELIS upsert failed for MPI patient {master_id}")


_HANDLERS = {
    "patient.synced": _handle_patient_synced,
}


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    consumer = BusConsumer(
        redis_url=REDIS_URL,
        group=GROUP,
        consumer=CONSUMER,
        handlers=_HANDLERS,
    )
    await consumer.run()
