"""
Tests for services/analytics/bus_consumer.py.

All tests run with REDIS_URL='' (set in conftest) so no real Redis connection
is attempted.
"""
import asyncio
import pytest
from datetime import datetime, timezone


# ── consume_loop ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consume_loop_disabled_without_redis_url(fresh_db, monkeypatch):
    import bus_consumer
    monkeypatch.setattr(bus_consumer, "REDIS_URL", "")
    await bus_consumer.consume_loop()   # should return immediately without error


# ── _ensure_event_counts_table ────────────────────────────────────────────────

def test_ensure_event_counts_table_creates_table(fresh_db):
    import bus_consumer
    from database import get_db
    bus_consumer._ensure_event_counts_table()
    with get_db() as db:
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='event_counts'"
        ).fetchone()
    assert row is not None


def test_ensure_event_counts_table_idempotent(fresh_db):
    """Calling twice does not raise."""
    import bus_consumer
    bus_consumer._ensure_event_counts_table()
    bus_consumer._ensure_event_counts_table()


# ── _record_event ──────────────────────────────────────────────────────────────

def test_record_event_inserts_row(fresh_db):
    import bus_consumer
    from database import get_db
    bus_consumer._ensure_event_counts_table()
    bus_consumer._record_event("patient.synced", "integration-hub", "2026-03-27T10:00:00")
    with get_db() as db:
        row = db.execute(
            "SELECT count,event_date FROM event_counts"
            " WHERE event_type='patient.synced' AND source='integration-hub'"
        ).fetchone()
    assert row is not None
    assert row["count"] == 1
    assert row["event_date"] == "2026-03-27"


def test_record_event_increments_count(fresh_db):
    import bus_consumer
    from database import get_db
    bus_consumer._ensure_event_counts_table()
    bus_consumer._record_event("lab_result.ready", "integration-hub", "2026-03-27T08:00:00")
    bus_consumer._record_event("lab_result.ready", "integration-hub", "2026-03-27T09:00:00")
    with get_db() as db:
        row = db.execute(
            "SELECT count FROM event_counts WHERE event_type='lab_result.ready'"
        ).fetchone()
    assert row["count"] == 2


def test_record_event_tracks_different_dates_separately(fresh_db):
    import bus_consumer
    from database import get_db
    bus_consumer._ensure_event_counts_table()
    bus_consumer._record_event("patient.synced", "hub", "2026-03-26T10:00:00")
    bus_consumer._record_event("patient.synced", "hub", "2026-03-27T10:00:00")
    with get_db() as db:
        rows = db.execute(
            "SELECT event_date,count FROM event_counts WHERE event_type='patient.synced'"
            " ORDER BY event_date"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["event_date"] == "2026-03-26"
    assert rows[1]["event_date"] == "2026-03-27"
    assert rows[0]["count"] == 1
    assert rows[1]["count"] == 1


def test_record_event_different_sources_tracked_separately(fresh_db):
    import bus_consumer
    from database import get_db
    bus_consumer._ensure_event_counts_table()
    bus_consumer._record_event("patient.synced", "integration-hub", "2026-03-27T10:00:00")
    bus_consumer._record_event("patient.synced", "hl7-service",     "2026-03-27T11:00:00")
    with get_db() as db:
        rows = db.execute(
            "SELECT source,count FROM event_counts WHERE event_type='patient.synced'"
            " ORDER BY source"
        ).fetchall()
    assert len(rows) == 2


def test_record_event_handles_missing_ts(fresh_db):
    """An empty ts falls back to today's date without raising."""
    import bus_consumer
    bus_consumer._ensure_event_counts_table()
    bus_consumer._record_event("unknown.event", "test", "")   # empty ts
    bus_consumer._record_event("unknown.event", "test", None)  # None ts — should not crash


def test_record_event_handles_short_ts(fresh_db):
    """A ts shorter than 10 characters uses it as-is (or falls back)."""
    import bus_consumer
    bus_consumer._ensure_event_counts_table()
    bus_consumer._record_event("x.event", "src", "2026")   # short but valid prefix
