"""
Live event stream — Server-Sent Events bridge over Redis Streams.

GET /api/events/stream   — SSE endpoint; streams openhis:events to the browser.

Consumers connect with EventSource('/admin/api/events/stream') and receive
every new event in near-real-time. The connection is kept alive with
comment keep-alive pings every 15 s.

This router does NOT use a consumer group — it reads forward from the
current tail so the admin UI only sees new events (not historical ones).

GET /api/events/recent   — last N events (default 100) as JSON, for
                           populating the dashboard on page load.
"""
import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

log = logging.getLogger("admin.events")

STREAM   = "openhis:events"
REDIS_URL: str = os.environ.get("REDIS_URL", "")

router = APIRouter(prefix="/api/events", tags=["events"])


def _redis_client():
    """Return an async Redis client (import lazily to avoid hard dependency)."""
    import redis.asyncio as aioredis
    return aioredis.from_url(REDIS_URL, decode_responses=True)


async def _sse_generator(last_id: str = "$") -> AsyncIterator[str]:
    """Yield SSE-formatted strings from the Redis stream."""
    if not REDIS_URL:
        yield "data: {\"error\": \"Redis not configured\"}\n\n"
        return

    r = _redis_client()
    try:
        # Start from the current tail unless a last_id was supplied
        cursor = last_id

        while True:
            try:
                results = await r.xread(
                    streams={STREAM: cursor},
                    count=20,
                    block=15_000,   # 15 s block — doubles as keep-alive heartbeat window
                )
                if not results:
                    # Timeout — send a keep-alive comment
                    yield ": keep-alive\n\n"
                    continue

                for _stream, messages in results:
                    for entry_id, fields in messages:
                        cursor = entry_id
                        payload = {
                            "id":      entry_id,
                            "type":    fields.get("type", ""),
                            "source":  fields.get("source", ""),
                            "payload": json.loads(fields.get("payload", "{}")),
                            "ts":      fields.get("ts", ""),
                        }
                        yield f"id: {entry_id}\n"
                        yield f"data: {json.dumps(payload)}\n\n"

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("SSE stream error: %s", exc)
                yield f"data: {{\"error\": \"{exc}\"}}\n\n"
                await asyncio.sleep(3)
    finally:
        await r.aclose()


@router.get("/stream")
async def event_stream(last_event_id: str = Query(default="$", alias="lastEventId")):
    """
    SSE endpoint — streams openhis:events to the caller.

    Pass `?lastEventId=<id>` to resume from a specific stream position.
    """
    return StreamingResponse(
        _sse_generator(last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering for SSE
        },
    )


@router.get("/recent")
async def recent_events(limit: int = Query(default=100, le=500)):
    """Return the most recent N events from the stream as JSON."""
    if not REDIS_URL:
        return []
    r = _redis_client()
    try:
        # XREVRANGE gives newest-first; we reverse for chronological order
        raw = await r.xrevrange(STREAM, count=limit)
        events = []
        for entry_id, fields in reversed(raw):
            events.append({
                "id":      entry_id,
                "type":    fields.get("type", ""),
                "source":  fields.get("source", ""),
                "payload": json.loads(fields.get("payload", "{}")),
                "ts":      fields.get("ts", ""),
            })
        return events
    finally:
        await r.aclose()
