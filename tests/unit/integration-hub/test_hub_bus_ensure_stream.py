"""Regression tests — hub ``ensure_stream()`` must never trim ``openhis:events``.

The hub used to XADD a sentinel entry with ``maxlen=1`` on every startup,
which trimmed the shared 50k-capped stream down to ~1 macro-node and wiped
the backlog of every lagging consumer group (analytics, hl7, ai-controller).
``xgroup_create(..., mkstream=True)`` already creates the stream, so
``ensure_stream()`` must not XADD at all.

Runs against fakeredis — no Docker, no network.
"""
import asyncio
import sys
from pathlib import Path

import pytest

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis not installed")
import fakeredis.aioredis  # noqa: E402

HUB_PATH = str(
    Path(__file__).parent.parent.parent.parent / "services" / "integration-hub"
)


def _clear_hub_modules() -> None:
    for mod in list(sys.modules.keys()):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


@pytest.fixture
def bus(monkeypatch):
    """Fresh ``app.bus`` import wired to an in-memory FakeRedis client."""
    # Truthy REDIS_URL so ensure_stream()/publish() do not short-circuit.
    monkeypatch.setenv("REDIS_URL", "redis://fake-bus-test:6379")

    if HUB_PATH not in sys.path:
        sys.path.insert(0, HUB_PATH)
    _clear_hub_modules()

    import app.bus as bus_mod

    bus_mod._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield bus_mod
    asyncio.run(bus_mod.close())
    _clear_hub_modules()


def test_ensure_stream_does_not_trim_existing_stream(bus):
    """ensure_stream() on a populated stream leaves XLEN unchanged."""

    async def scenario() -> tuple[int, int]:
        r = await bus.get_client()
        n_entries = 25
        for i in range(n_entries):
            await r.xadd(bus.STREAM, {"type": "test.event", "seq": str(i)})
        before = await r.xlen(bus.STREAM)
        assert before == n_entries  # sanity

        await bus.ensure_stream()  # simulates a hub restart
        after = await r.xlen(bus.STREAM)
        return before, after

    before, after = asyncio.run(scenario())
    assert after == before, (
        f"ensure_stream() trimmed the shared stream: XLEN {before} -> {after}"
    )


def test_ensure_stream_creates_stream_and_groups_when_absent(bus):
    """mkstream=True must cover the stream-does-not-exist-yet case."""

    async def scenario() -> tuple[bool, set[str]]:
        r = await bus.get_client()
        assert await r.exists(bus.STREAM) == 0  # sanity: nothing yet

        await bus.ensure_stream()

        exists = bool(await r.exists(bus.STREAM))
        groups = {g["name"] for g in await r.xinfo_groups(bus.STREAM)}
        return exists, groups

    exists, groups = asyncio.run(scenario())
    assert exists, "ensure_stream() did not create the stream"
    assert groups == set(bus.CONSUMER_GROUPS)


def test_ensure_stream_is_idempotent_and_adds_no_entries(bus):
    """Repeated startups add zero entries (no sentinel XADD)."""

    async def scenario() -> int:
        r = await bus.get_client()
        await bus.ensure_stream()
        await bus.ensure_stream()
        return await r.xlen(bus.STREAM)

    assert asyncio.run(scenario()) == 0
