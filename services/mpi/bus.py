"""
bus — Redis Streams event publisher for the MPI REST plane (DEF-010).

Synchronous mirror of the integration-hub publisher
(services/integration-hub/app/bus.py): events are XADDed to the shared
`openhis:events` stream with the same field schema:

    type    — dot-namespaced event type, e.g. "patient.synced"
    source  — originating service ("mpi")
    payload — JSON-encoded dict with event-specific data
    ts      — ISO-8601 UTC timestamp

MPI's REST routes are sync functions (they run in FastAPI's threadpool), so
this module uses the blocking redis client. The async publisher used by the
bus-consumer path lives in openhis_sdk.bus.publish_event.

Failure contract: publish() NEVER raises. When REDIS_URL is unset or Redis
is unreachable it logs and returns None — bus publication must never fail
an API request.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis

from openhis_sdk.bus import MAXLEN as BUS_MAXLEN

STREAM = "openhis:events"

log = logging.getLogger("mpi.bus")

# Bound request latency when the broker is down — a dead Redis must not hang
# the API worker for the OS-level connect timeout.
_SOCKET_TIMEOUT = 2.0

_client: redis.Redis | None = None
_client_url: str = ""


def _get_client(url: str) -> redis.Redis:
    """Return a cached blocking client, rebuilt if REDIS_URL changed."""
    global _client, _client_url
    if _client is None or _client_url != url:
        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT,
            socket_timeout=_SOCKET_TIMEOUT,
        )
        _client_url = url
    return _client


def publish(event_type: str, payload: dict[str, Any], source: str = "mpi") -> str | None:
    """
    Publish an event to the openhis:events stream (fire-and-forget).

    Returns the stream entry ID on success, or None when REDIS_URL is unset
    or Redis is unreachable. Never raises.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        log.debug("Bus publish skipped (%s): REDIS_URL not set", event_type)
        return None
    try:
        r = _get_client(redis_url)
        entry_id = r.xadd(
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
