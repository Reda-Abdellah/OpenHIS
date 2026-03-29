"""
Redis Streams event bus helpers — canonical source for OpenHIS services.

Usage — publishing:
    from openhis_sdk.bus import publish_event
    await publish_event(redis_client, "patient.synced", {"mrn": "123", "omrs_id": "..."})

Usage — consuming:
    from openhis_sdk.bus import BusConsumer

    consumer = BusConsumer(
        redis_url=REDIS_URL,
        group="mpi",
        consumer="mpi-1",
        handlers={"patient.synced": handle_patient_synced},
    )
    await consumer.run()   # blocks; run as asyncio Task
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis

log = logging.getLogger("openhis_sdk.bus")

STREAM = "openhis:events"
EventHandler = Callable[[dict], Awaitable[None]]


async def publish_event(client: aioredis.Redis, event_type: str, payload: dict) -> None:
    """Publish a typed event to the openhis:events stream."""
    await client.xadd(
        STREAM,
        {"type": event_type, "payload": json.dumps(payload)},
        maxlen=50_000,
        approximate=True,
    )
    log.debug("Published %s", event_type)


class BusConsumer:
    """
    Long-running Redis Streams consumer.

    Each service should instantiate one BusConsumer with its group name and
    a dict mapping event types to async handler functions.
    """

    def __init__(
        self,
        redis_url: str,
        group: str,
        consumer: str,
        handlers: dict[str, EventHandler],
        batch: int = 20,
        block_ms: int = 5_000,
    ) -> None:
        self._redis_url = redis_url
        self._group = group
        self._consumer = consumer
        self._handlers = handlers
        self._batch = batch
        self._block_ms = block_ms
        self._client: aioredis.Redis | None = None

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def _ensure_group(self) -> None:
        try:
            await self._get_client().xgroup_create(STREAM, self._group, id="$", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                log.warning("xgroup_create: %s", e)

    async def _process(self, entry_id: str, fields: dict) -> None:
        event_type = fields.get("type", "")
        handler = self._handlers.get(event_type)
        if handler is None:
            return
        try:
            payload = json.loads(fields.get("payload", "{}"))
            await handler(payload)
        except Exception as exc:
            log.error("Error handling %s (%s): %s", event_type, entry_id, exc)

    async def run(self) -> None:
        """Main consumer loop — runs until the task is cancelled."""
        if not self._redis_url:
            log.info("REDIS_URL not set — bus consumer disabled")
            return

        await self._ensure_group()
        log.info("Bus consumer started (stream=%s group=%s)", STREAM, self._group)

        while True:
            try:
                results = await self._get_client().xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={STREAM: ">"},
                    count=self._batch,
                    block=self._block_ms,
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for entry_id, fields in messages:
                        await self._process(entry_id, fields)
                        await self._get_client().xack(STREAM, self._group, entry_id)
            except asyncio.CancelledError:
                log.info("Bus consumer stopping")
                break
            except Exception as exc:
                log.error("Bus consumer error: %s — retrying in 5 s", exc)
                await asyncio.sleep(5)
