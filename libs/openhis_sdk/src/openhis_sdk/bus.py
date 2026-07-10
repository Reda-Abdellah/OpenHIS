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

Delivery semantics (see docs/adr/0005-bus-dead-letter-semantics.md):
    * Entries are XACKed ONLY after the handler completes successfully.
    * On handler failure the entry stays in the group's pending list (PEL)
      and is redelivered via XAUTOCLAIM once it has been idle >= ``idle_ms``.
    * After ``max_delivery`` failed deliveries the entry is parked on the
      dead-letter stream ``openhis:events:dlq`` (origin_id/type/payload/
      error/group) and the original is acked so the group can move on.
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis

log = logging.getLogger("openhis_sdk.bus")

STREAM = "openhis:events"
# Single authoritative cap for the openhis:events stream.
# All publishers (SDK and services that xadd directly) must use this value.
MAXLEN = 50_000

# Dead-letter stream: entries that failed handling `max_delivery` times are
# parked here (origin_id / type / payload / error / group) so operators can
# inspect and replay them. Bounded so poison floods cannot grow unbounded.
DLQ_STREAM = "openhis:events:dlq"
DLQ_MAXLEN = 10_000

EventHandler = Callable[[dict], Awaitable[None]]


async def publish_event(client: aioredis.Redis, event_type: str, payload: dict) -> None:
    """Publish a typed event to the openhis:events stream."""
    await client.xadd(
        STREAM,
        {"type": event_type, "payload": json.dumps(payload)},
        maxlen=MAXLEN,
        approximate=True,
    )
    log.debug("Published %s", event_type)


async def send_to_dlq(
    client: aioredis.Redis,
    group: str,
    entry_id: str,
    fields: dict,
    error: Exception | str,
) -> None:
    """Park a poison entry on the dead-letter stream. Never raises."""
    err_text = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
    try:
        await client.xadd(
            DLQ_STREAM,
            {
                "origin_id": entry_id,
                "type": fields.get("type", ""),
                "payload": fields.get("payload", "{}"),
                "error": err_text,
                "group": group,
            },
            maxlen=DLQ_MAXLEN,
            approximate=True,
        )
    except Exception as dlq_exc:
        log.error("DLQ write failed for %s: %s", entry_id, dlq_exc)


class BusConsumer:
    """
    Long-running Redis Streams consumer.

    Each service should instantiate one BusConsumer with its group name and
    a dict mapping event types to async handler functions. Entries are acked
    only after successful handling; failures stay pending and are retried up
    to ``max_delivery`` times before landing on ``openhis:events:dlq``.
    """

    def __init__(
        self,
        redis_url: str,
        group: str,
        consumer: str,
        handlers: dict[str, EventHandler],
        batch: int = 20,
        block_ms: int = 5_000,
        max_delivery: int = 5,
        idle_ms: int = 30_000,
        fallback_handler: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._group = group
        self._consumer = consumer
        self._handlers = handlers
        self._batch = batch
        self._block_ms = block_ms
        self._max_delivery = max_delivery
        self._idle_ms = idle_ms
        # Called with the raw field mapping for entries whose type has no
        # dedicated handler (e.g. analytics tallies every event verbatim).
        self._fallback_handler = fallback_handler
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

    async def _process(self, entry_id: str, fields: dict) -> bool:
        """
        Dispatch one entry to its handler.

        Returns True on success (including "no handler registered").
        Handler exceptions are logged and RE-RAISED so the caller can keep
        the entry pending instead of acking it.
        """
        event_type = fields.get("type", "")
        handler = self._handlers.get(event_type)
        if handler is not None:
            try:
                payload = json.loads(fields.get("payload", "{}"))
                await handler(payload)
            except Exception as exc:
                log.error("Error handling %s (%s): %s", event_type, entry_id, exc)
                raise
            return True
        if self._fallback_handler is not None:
            try:
                await self._fallback_handler(fields)
            except Exception as exc:
                log.error("Error handling %s (%s) in fallback: %s", event_type, entry_id, exc)
                raise
        return True

    async def _delivery_count(self, entry_id: str) -> int:
        """Return how many times the entry has been delivered (XPENDING)."""
        try:
            pending = await self._get_client().xpending_range(
                STREAM, self._group, min=entry_id, max=entry_id, count=1
            )
        except (aioredis.RedisError, OSError) as exc:
            log.warning("xpending_range failed for %s: %s", entry_id, exc)
            return 0
        if not pending:
            return 0
        return int(pending[0].get("times_delivered", 1))

    async def _handle_entry(self, entry_id: str, fields: dict) -> None:
        """
        Process one entry; XACK ONLY on success.

        On failure the entry stays in the PEL for redelivery. Once it has
        been delivered ``max_delivery`` times, it is parked on the DLQ and
        the original entry is acked so the group is not blocked forever.
        """
        try:
            await self._process(entry_id, fields)
        except Exception as exc:
            delivered = await self._delivery_count(entry_id)
            if delivered >= self._max_delivery:
                log.error(
                    "Entry %s (%s) failed %d/%d deliveries — moving to %s",
                    entry_id, fields.get("type", ""), delivered, self._max_delivery, DLQ_STREAM,
                )
                await send_to_dlq(self._get_client(), self._group, entry_id, fields, exc)
                await self._get_client().xack(STREAM, self._group, entry_id)
            else:
                log.warning(
                    "Entry %s (%s) failed delivery %d/%d — left pending for retry",
                    entry_id, fields.get("type", ""), delivered, self._max_delivery,
                )
            return
        await self._get_client().xack(STREAM, self._group, entry_id)

    async def _reclaim_pending(self) -> list[tuple[str, dict]]:
        """
        XAUTOCLAIM entries idle >= idle_ms back to this consumer.

        Picks up entries whose previous delivery failed (ours or a crashed
        peer's) so they get retried. Returns a list of (entry_id, fields).
        """
        try:
            result = await self._get_client().xautoclaim(
                STREAM,
                self._group,
                self._consumer,
                min_idle_time=self._idle_ms,
                start_id="0-0",
                count=self._batch,
            )
        except aioredis.ResponseError as exc:
            # NOGROUP can race group creation on a fresh stream — not fatal.
            log.warning("xautoclaim failed: %s", exc)
            return []
        # Redis 7 replies [cursor, entries, deleted]; Redis 6.2 [cursor, entries].
        entries = result[1] if len(result) >= 2 else []
        # Entries trimmed from the stream but still in the PEL come back with
        # nil fields — nothing to process, skip them.
        return [(eid, fields) for eid, fields in entries if fields]

    async def run(self) -> None:
        """Main consumer loop — runs until the task is cancelled."""
        if not self._redis_url:
            log.info("REDIS_URL not set — bus consumer disabled")
            return

        await self._ensure_group()
        log.info("Bus consumer started (stream=%s group=%s)", STREAM, self._group)

        while True:
            try:
                for entry_id, fields in await self._reclaim_pending():
                    await self._handle_entry(entry_id, fields)

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
                        await self._handle_entry(entry_id, fields)
            except asyncio.CancelledError:
                log.info("Bus consumer stopping")
                break
            except aioredis.TimeoutError:
                # An empty blocking read timing out is not an error — some
                # redis-py versions surface XREADGROUP's block= as a socket
                # read timeout instead of returning an empty result.
                continue
            except Exception as exc:
                log.error("Bus consumer error: %s — retrying in 5 s", exc)
                await asyncio.sleep(5)
