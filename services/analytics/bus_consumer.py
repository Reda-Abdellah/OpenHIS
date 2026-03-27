"""
bus_consumer — Analytics subscriber for the openhis:events Redis stream.

Records lightweight event tallies so the analytics dashboard can show
real-time activity counts broken down by event type and date, without
needing to query every upstream service.

Consumer group: analytics
Consumer name:  analytics-1

Events recorded: all (type is stored verbatim so new events appear
automatically in the dashboard without code changes).
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import redis.asyncio as aioredis

from database import get_db

log = logging.getLogger("analytics.bus")

STREAM   = "openhis:events"
GROUP    = "analytics"
CONSUMER = "analytics-1"
BLOCK_MS = 5_000
BATCH    = 50

REDIS_URL: str = os.environ.get("REDIS_URL", "")

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


def _ensure_event_counts_table() -> None:
    """Add the event_counts table if it doesn't exist yet."""
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS event_counts (
                event_date  TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'unknown',
                count       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (event_date, event_type, source)
            )
        """)


def _record_event(event_type: str, source: str, ts: str) -> None:
    try:
        event_date = ts[:10] if ts else datetime.now(timezone.utc).date().isoformat()
    except Exception:
        event_date = datetime.now(timezone.utc).date().isoformat()

    with get_db() as db:
        db.execute(
            """
            INSERT INTO event_counts (event_date, event_type, source, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(event_date, event_type, source)
            DO UPDATE SET count = count + 1
            """,
            (event_date, event_type, source),
        )


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    if not REDIS_URL:
        log.info("REDIS_URL not set — bus consumer disabled")
        return

    _ensure_event_counts_table()
    r = _get_client()

    try:
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            log.warning("xgroup_create: %s", e)

    log.info("Analytics bus consumer started (stream=%s group=%s)", STREAM, GROUP)

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
                    event_type = fields.get("type", "unknown")
                    source     = fields.get("source", "unknown")
                    ts         = fields.get("ts", "")
                    _record_event(event_type, source, ts)
                    await r.xack(STREAM, GROUP, entry_id)

        except asyncio.CancelledError:
            log.info("Analytics bus consumer stopping")
            break
        except Exception as exc:
            log.error("Bus consumer error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)
