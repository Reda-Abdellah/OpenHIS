"""
bus — Redis Streams event publisher for the Integration Hub.

All cross-service events flow through the `openhis:events` stream.
Consumers (MPI, Analytics, HL7) read via consumer groups so each
service sees every event exactly once.

Event schema (fields in each stream entry):
    type    — dot-namespaced event type, e.g. "patient.synced"
    source  — originating service, e.g. "integration-hub"
    payload — JSON-encoded dict with event-specific data
    ts      — ISO-8601 UTC timestamp

Usage:
    await bus.publish("patient.synced", {"omrs_id": "...", "oe_id": "..."})
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from openhis_sdk.bus import MAXLEN as BUS_MAXLEN

from app.config import REDIS_URL

STREAM = "openhis:events"
CONSUMER_GROUPS = ["mpi", "analytics", "hl7", "ai-controller"]

log = logging.getLogger("hub.bus")

_client: aioredis.Redis | None = None


async def get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


async def ensure_stream() -> None:
    """Create stream and consumer groups if they don't exist yet."""
    if not REDIS_URL:
        return
    try:
        r = await get_client()
        # mkstream=True below creates the stream if it does not exist yet.
        # Never XADD here: a sentinel entry with a small maxlen would trim
        # the shared 50k-capped stream on every hub restart, dropping the
        # backlog of every lagging consumer group.
        for group in CONSUMER_GROUPS:
            try:
                await r.xgroup_create(STREAM, group, id="0", mkstream=True)
                log.info("Created consumer group '%s' on %s", group, STREAM)
            except aioredis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise
    except Exception as exc:
        log.warning("Bus init failed (Redis may be unavailable): %s", exc)


async def publish(event_type: str, payload: dict[str, Any], source: str = "integration-hub") -> str | None:
    """
    Publish an event to the openhis:events stream.

    Returns the stream entry ID on success, or None if Redis is unavailable.
    """
    if not REDIS_URL:
        return None
    try:
        r = await get_client()
        entry_id = await r.xadd(
            STREAM,
            {
                "type": event_type,
                "source": source,
                "payload": json.dumps(payload),
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=BUS_MAXLEN,   # single authoritative cap — openhis_sdk.bus.MAXLEN
            approximate=True,
        )
        log.debug("Published %s → %s", event_type, entry_id)
        return entry_id
    except Exception as exc:
        log.warning("Bus publish failed (%s): %s", event_type, exc)
        return None


async def close() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None
