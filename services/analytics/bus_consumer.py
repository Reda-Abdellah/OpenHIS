"""
bus_consumer — Analytics subscriber for the openhis:events Redis stream.

Records lightweight event tallies so the analytics dashboard can show
real-time activity counts broken down by event type and date, without
needing to query every upstream service.

Consumer group: analytics
Consumer name:  analytics-1

Events recorded: all (type is stored verbatim so new events appear
automatically in the dashboard without code changes) — consumed through
the SDK BusConsumer's fallback_handler, which receives the raw field
mapping for every entry. Entries are acked only after successful
handling; poison entries land on openhis:events:dlq after max_delivery
attempts (see docs/adr/0005-bus-dead-letter-semantics.md).
"""
import logging
import os
from datetime import datetime, timezone

from database import get_db
from openhis_sdk.bus import BusConsumer

log = logging.getLogger("analytics.bus")

GROUP    = "analytics"
CONSUMER = "analytics-1"
BATCH    = 50

REDIS_URL: str = os.environ.get("REDIS_URL", "")


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


async def _record_fields(fields: dict) -> None:
    """Fallback handler: tally any event from its raw field mapping."""
    _record_event(
        fields.get("type", "unknown"),
        fields.get("source", "unknown"),
        fields.get("ts", ""),
    )


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    if not REDIS_URL:
        log.info("REDIS_URL not set — bus consumer disabled")
        return

    _ensure_event_counts_table()
    consumer = BusConsumer(
        redis_url=REDIS_URL,
        group=GROUP,
        consumer=CONSUMER,
        handlers={},
        batch=BATCH,
        fallback_handler=_record_fields,
    )
    await consumer.run()
