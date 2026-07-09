"""Payload-contract tests — the hub's lab bus events.

Pins the EXACT field set each event carries so publisher ↔ consumer
drift fails CI instead of surfacing at runtime. Consumer-side readers
of these contracts: the hl7 bus consumer (``_handle_lab_result_ready``)
and the analytics bus consumer.

Contracts pinned here:

  lab_order.routed    worker._sync_orders   → {omrs_id, oe_id}
  lab_result.ready    worker._sync_results  → {oe_id, subject}

If a field changes here, update the publisher(s), the consumers, and
``openhis.service.json`` ``bus.publishes`` together.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

HUB_PATH = str(
    Path(__file__).parent.parent.parent.parent / "services" / "integration-hub"
)
MANIFEST_PATH = Path(HUB_PATH) / "openhis.service.json"


# ── the contracts ───────────────────────────────────────────────────────────

LAB_ORDER_ROUTED_FIELDS = {"omrs_id", "oe_id"}
LAB_RESULT_READY_FIELDS = {"oe_id", "subject"}


# ── helpers / fixtures ──────────────────────────────────────────────────────


def _clear_hub_modules() -> None:
    for mod in list(sys.modules.keys()):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


@pytest.fixture
def worker(tmp_path, monkeypatch):
    """Fresh ``app.worker`` import with network-free env (no Redis)."""
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "hub-audit.db"))
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("OPENMRS_URL", "http://openmrs-contract-test:9996")
    monkeypatch.setenv("OPENELIS_URL", "http://openelis-contract-test:9996")
    monkeypatch.setenv("ODOO_URL", "http://odoo-contract-test:9996")
    monkeypatch.setenv("ODOO_DB", "odoo")
    monkeypatch.setenv("POLL_INTERVAL_S", "99999")
    monkeypatch.delenv("REDIS_URL", raising=False)

    if HUB_PATH not in sys.path:
        sys.path.insert(0, HUB_PATH)
    _clear_hub_modules()

    import app.worker as worker_mod
    yield worker_mod
    _clear_hub_modules()


def _capture_publishes(monkeypatch, bus_module) -> list[tuple[str, dict]]:
    """Replace ``bus.publish`` with a collecting stub."""
    captured: list[tuple[str, dict]] = []

    async def fake_publish(
        event_type: str, payload: dict, source: str = "integration-hub",
    ) -> str:
        captured.append((event_type, payload))
        return "0-0"

    monkeypatch.setattr(bus_module, "publish", fake_publish)
    return captured


def _silence_audit(monkeypatch, audit_module) -> None:
    async def no_audit(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(audit_module, "log_event", no_audit)


def _events_by_type(captured: list[tuple[str, dict]]) -> dict[str, dict]:
    return {event_type: payload for event_type, payload in captured}


SR = {
    "id": "omrs-sr-1",
    "resourceType": "ServiceRequest",
    "status": "active",
    "intent": "order",
    "subject": {"reference": "Patient/p-1"},
    "code": {"coding": [{"system": "http://loinc.org", "code": "2160-0",
                         "display": "Creatinine"}]},
}


# ── worker (OpenMRS → OpenELIS poll path) ───────────────────────────────────


class TestWorkerLabOrderRouted:
    def test_lab_order_routed_exact_field_set(self, worker, monkeypatch):
        published = _capture_publishes(monkeypatch, worker.bus)
        _silence_audit(monkeypatch, worker.audit)

        async def fake_orders() -> list[dict]:
            return [dict(SR)]

        async def fake_create(sr: dict) -> str:
            return "oe-77"

        monkeypatch.setattr(
            worker.openmrs, "get_active_service_requests", fake_orders)
        monkeypatch.setattr(
            worker.openelis, "create_service_request", fake_create)

        assert asyncio.run(worker._sync_orders()) == 1

        events = _events_by_type(published)
        assert "lab_order.routed" in events
        payload = events["lab_order.routed"]
        assert set(payload) == LAB_ORDER_ROUTED_FIELDS, (
            "worker lab_order.routed payload drifted — update the "
            "consumer contracts before changing this"
        )
        assert payload["omrs_id"] == "omrs-sr-1"
        assert payload["oe_id"] == "oe-77"


class TestWorkerLabResultReady:
    def test_lab_result_ready_exact_field_set(self, worker, monkeypatch):
        published = _capture_publishes(monkeypatch, worker.bus)
        _silence_audit(monkeypatch, worker.audit)

        async def fake_reports() -> list[dict]:
            return [{"id": "dr-9", "resourceType": "DiagnosticReport",
                     "subject": {"reference": "Patient/p-1"}}]

        async def fake_post(dr: dict) -> bool:
            return True

        monkeypatch.setattr(
            worker.openelis, "get_completed_reports", fake_reports)
        monkeypatch.setattr(
            worker.openmrs, "post_diagnostic_report", fake_post)

        assert asyncio.run(worker._sync_results()) == 1
        events = _events_by_type(published)
        assert "lab_result.ready" in events
        payload = events["lab_result.ready"]
        assert set(payload) == LAB_RESULT_READY_FIELDS, (
            "lab_result.ready payload drifted — the hl7 "
            "_handle_lab_result_ready reads exactly {oe_id, subject}"
        )
        assert payload["oe_id"] == "dr-9"
        assert payload["subject"] == "Patient/p-1"


def test_manifest_declares_all_published_lab_topics():
    """Every lab topic published by worker.py is in bus.publishes."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    publishes = set(manifest["bus"]["publishes"])
    expected = {
        "lab_order.routed",
        "lab_result.ready",
    }
    missing = expected - publishes
    assert not missing, (
        f"openhis.service.json bus.publishes is missing {sorted(missing)}"
    )
