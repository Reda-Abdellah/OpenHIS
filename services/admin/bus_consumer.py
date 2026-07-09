"""
bus_consumer — admin subscriber for the openhis:events Redis stream.

Bridges platform bus events into the admin audit log so operators have a
single audit trail in the management plane (DEF-002 / V&V S1.7: a patient
created in MPI must surface in admin /api/audit as action="patient.synced"
with the MRN in the detail column).

Consumer group: admin
Consumer name:  admin-1

Started as an asyncio task from main.py's lifespan when REDIS_URL is set;
BusConsumer.run() no-ops gracefully when it is empty (unit tests, minimal
dev stacks).
"""
import logging
import os

from openhis_sdk.bus import BusConsumer

from database import audit

log = logging.getLogger("admin.bus")

REDIS_URL: str = os.environ.get("REDIS_URL", "")


async def handle_patient_synced(payload: dict) -> None:
    """Write an audit row for a patient.synced bus event.

    Expected payload keys: master_id, mrn (extras are ignored).
    The MRN must land in `detail` — the S1.7 e2e assertion greps for it.
    """
    master_id = payload.get("master_id") or ""
    mrn = payload.get("mrn") or ""
    audit(
        "system",
        "patient.synced",
        target=master_id or None,
        detail=f"MRN={mrn}",
    )
    log.info(
        "patient.synced audited",
        extra={"master_id": master_id, "mrn": mrn},
    )


def build_consumer() -> BusConsumer:
    """Construct the admin bus consumer (factory shared by main.py and tests)."""
    return BusConsumer(
        redis_url=REDIS_URL,
        group="admin",
        consumer="admin-1",
        handlers={"patient.synced": handle_patient_synced},
    )
