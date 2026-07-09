"""
Tests for openhis_sdk.bus — ack-on-success + dead-letter semantics (ADR 0005).

Runs against fakeredis (in-memory, async) — no Docker, no network. The
BusConsumer's lazy client is replaced with a FakeRedis instance so the
real consumer loop (xreadgroup / xautoclaim / xpending / xack / DLQ xadd)
is exercised end to end.

Prescribed scenarios:
  (a) handler raises   -> entry remains in the pending list, NOT acked
  (b) next loop pass   -> the same entry is redelivered to the same consumer
  (c) max_delivery hit -> entry lands on openhis:events:dlq, original acked
  (d) handler succeeds -> entry acked immediately, no DLQ entry
"""
import asyncio
import contextlib
import json

import fakeredis.aioredis
import pytest

from openhis_sdk.bus import (
    DLQ_STREAM,
    STREAM,
    BusConsumer,
    publish_event,
    send_to_dlq,
)

GROUP = "test-group"
CONSUMER = "test-consumer-1"


# ── helpers ────────────────────────────────────────────────────────────────────


def _fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _make_consumer(
    client: fakeredis.aioredis.FakeRedis,
    handlers: dict,
    **kwargs,
) -> BusConsumer:
    """BusConsumer wired to fakeredis (redis_url must be truthy for run())."""
    kwargs.setdefault("block_ms", 10)
    consumer = BusConsumer(
        redis_url="redis://fake:6379",
        group=GROUP,
        consumer=CONSUMER,
        handlers=handlers,
        **kwargs,
    )
    consumer._client = client
    return consumer


async def _seed_group(client: fakeredis.aioredis.FakeRedis) -> None:
    """Create the consumer group at id=0 so pre-published entries are seen."""
    await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)


async def _eventually(predicate, timeout: float = 3.0) -> bool:
    """Poll an async predicate until true or timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@contextlib.asynccontextmanager
async def _running(consumer: BusConsumer):
    task = asyncio.create_task(consumer.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _pending_entries(client: fakeredis.aioredis.FakeRedis) -> list[dict]:
    return await client.xpending_range(STREAM, GROUP, min="-", max="+", count=100)


# ── publish_event ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_event_adds_typed_entry():
    client = _fake_redis()
    await publish_event(client, "patient.synced", {"mrn": "M-1"})
    entries = await client.xrange(STREAM, "-", "+")
    assert len(entries) == 1
    _eid, fields = entries[0]
    assert fields["type"] == "patient.synced"
    assert json.loads(fields["payload"]) == {"mrn": "M-1"}


# ── (d) handler succeeds -> acked, no DLQ ─────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_entry_is_acked_and_no_dlq():
    client = _fake_redis()
    await _seed_group(client)
    seen: list[dict] = []

    async def handler(payload: dict) -> None:
        seen.append(payload)

    consumer = _make_consumer(client, {"patient.synced": handler})
    await publish_event(client, "patient.synced", {"mrn": "OK-1"})

    async with _running(consumer):
        assert await _eventually(lambda: _truthy(seen))
        assert await _eventually(_no_pending(client))

    assert seen == [{"mrn": "OK-1"}]
    assert await client.xlen(DLQ_STREAM) == 0


async def _truthy(value) -> bool:
    return bool(value)


def _no_pending(client):
    async def check() -> bool:
        return not await _pending_entries(client)
    return check


# ── (a) handler raises -> stays pending, not acked ────────────────────────────


@pytest.mark.asyncio
async def test_failed_entry_stays_pending_and_unacked():
    client = _fake_redis()
    await _seed_group(client)
    calls: list[dict] = []

    async def handler(payload: dict) -> None:
        calls.append(payload)
        raise RuntimeError("boom")

    # Default idle_ms (30 s) — entry will NOT be reclaimed during the test,
    # so a single failed delivery must leave it parked in the PEL.
    consumer = _make_consumer(client, {"lab_result.ready": handler})
    await publish_event(client, "lab_result.ready", {"oe_id": "DR-1"})

    async with _running(consumer):
        assert await _eventually(lambda: _truthy(calls))
        await asyncio.sleep(0.05)   # give the loop a chance to (wrongly) ack

    pending = await _pending_entries(client)
    assert len(pending) == 1
    assert pending[0]["consumer"] == CONSUMER
    assert pending[0]["times_delivered"] == 1
    assert len(calls) == 1
    assert await client.xlen(DLQ_STREAM) == 0


# ── (b) failed entry is redelivered to the same consumer ──────────────────────


@pytest.mark.asyncio
async def test_failed_entry_redelivered_to_same_consumer():
    client = _fake_redis()
    await _seed_group(client)
    calls: list[dict] = []

    async def handler(payload: dict) -> None:
        calls.append(payload)
        raise RuntimeError("boom")

    # idle_ms=0 -> XAUTOCLAIM reclaims the pending entry on the next pass.
    # max_delivery high enough that the DLQ path is not hit.
    consumer = _make_consumer(
        client, {"lab_result.ready": handler}, idle_ms=0, max_delivery=100
    )
    await publish_event(client, "lab_result.ready", {"oe_id": "DR-2"})

    async with _running(consumer):
        assert await _eventually(lambda: _truthy(len(calls) >= 3))

    pending = await _pending_entries(client)
    assert len(pending) == 1
    assert pending[0]["consumer"] == CONSUMER
    assert pending[0]["times_delivered"] >= 3
    assert await client.xlen(DLQ_STREAM) == 0


# ── (c) after max_delivery attempts -> DLQ + original acked ───────────────────


@pytest.mark.asyncio
async def test_poison_entry_lands_on_dlq_after_max_delivery_and_is_acked():
    client = _fake_redis()
    await _seed_group(client)
    calls: list[dict] = []

    async def handler(payload: dict) -> None:
        calls.append(payload)
        raise ValueError("poison payload")

    consumer = _make_consumer(
        client, {"lab_result.ready": handler}, idle_ms=0, max_delivery=3
    )
    await publish_event(client, "lab_result.ready", {"oe_id": "DR-POISON"})
    origin_entries = await client.xrange(STREAM, "-", "+")
    origin_id = origin_entries[0][0]

    async def dlq_has_entry() -> bool:
        return await client.xlen(DLQ_STREAM) > 0

    async with _running(consumer):
        assert await _eventually(dlq_has_entry)
        await asyncio.sleep(0.05)   # let any (wrong) extra redelivery surface

    # Exactly one DLQ entry mirroring the original
    dlq = await client.xrange(DLQ_STREAM, "-", "+")
    assert len(dlq) == 1
    _dlq_id, dlq_fields = dlq[0]
    assert dlq_fields["origin_id"] == origin_id
    assert dlq_fields["type"] == "lab_result.ready"
    assert json.loads(dlq_fields["payload"]) == {"oe_id": "DR-POISON"}
    assert "ValueError" in dlq_fields["error"]
    assert "poison payload" in dlq_fields["error"]
    assert dlq_fields["group"] == GROUP

    # Original acked exactly once -> PEL empty; handler ran max_delivery times
    assert await _pending_entries(client) == []
    assert len(calls) == 3


# ── unknown event types are acked (no handler == success) ─────────────────────


@pytest.mark.asyncio
async def test_unknown_event_type_is_acked_without_dlq():
    client = _fake_redis()
    await _seed_group(client)

    consumer = _make_consumer(client, {"patient.synced": _never_called})
    await publish_event(client, "pharmacy.order.sent", {"id": "rx-1"})

    async with _running(consumer):
        assert await _eventually(_no_pending(client))

    assert await client.xlen(DLQ_STREAM) == 0


async def _never_called(_payload: dict) -> None:  # pragma: no cover
    raise AssertionError("handler must not be called for unknown types")


# ── fallback handler (analytics-style catch-all) ──────────────────────────────


@pytest.mark.asyncio
async def test_fallback_handler_receives_raw_fields_for_unhandled_types():
    client = _fake_redis()
    await _seed_group(client)
    seen: list[dict] = []

    async def fallback(fields: dict) -> None:
        seen.append(fields)

    consumer = _make_consumer(client, {}, fallback_handler=fallback)
    await client.xadd(
        STREAM,
        {"type": "dicom.stored", "source": "orthanc", "payload": "{}"},
    )

    async with _running(consumer):
        assert await _eventually(lambda: _truthy(seen))
        assert await _eventually(_no_pending(client))

    assert seen[0]["type"] == "dicom.stored"
    assert seen[0]["source"] == "orthanc"


@pytest.mark.asyncio
async def test_fallback_handler_failure_follows_dlq_semantics():
    client = _fake_redis()
    await _seed_group(client)

    async def fallback(_fields: dict) -> None:
        raise RuntimeError("tally failed")

    consumer = _make_consumer(
        client, {}, fallback_handler=fallback, idle_ms=0, max_delivery=2
    )
    await client.xadd(STREAM, {"type": "anything.event", "payload": "{}"})

    async def dlq_has_entry() -> bool:
        return await client.xlen(DLQ_STREAM) > 0

    async with _running(consumer):
        assert await _eventually(dlq_has_entry)

    dlq = await client.xrange(DLQ_STREAM, "-", "+")
    assert len(dlq) == 1
    assert dlq[0][1]["type"] == "anything.event"
    assert "tally failed" in dlq[0][1]["error"]
    assert await _pending_entries(client) == []


# ── malformed payload is poison too ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_json_payload_eventually_dead_letters():
    client = _fake_redis()
    await _seed_group(client)

    async def handler(_payload: dict) -> None:  # pragma: no cover
        raise AssertionError("handler must not run on undecodable payload")

    consumer = _make_consumer(
        client, {"patient.synced": handler}, idle_ms=0, max_delivery=2
    )
    await client.xadd(STREAM, {"type": "patient.synced", "payload": "{not-json"})

    async def dlq_has_entry() -> bool:
        return await client.xlen(DLQ_STREAM) > 0

    async with _running(consumer):
        assert await _eventually(dlq_has_entry)

    dlq = await client.xrange(DLQ_STREAM, "-", "+")
    assert dlq[0][1]["payload"] == "{not-json"
    assert await _pending_entries(client) == []


# ── send_to_dlq never raises ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_to_dlq_swallows_redis_failures():
    class _BrokenClient:
        async def xadd(self, *args, **kwargs):
            raise ConnectionError("redis down")

    # Must not raise — DLQ writes are best-effort by design
    await send_to_dlq(_BrokenClient(), GROUP, "1-1", {"type": "x"}, RuntimeError("boom"))


# ── run() guard ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_returns_immediately_without_redis_url():
    consumer = BusConsumer(
        redis_url="",
        group=GROUP,
        consumer=CONSUMER,
        handlers={},
    )
    await asyncio.wait_for(consumer.run(), timeout=2.0)
