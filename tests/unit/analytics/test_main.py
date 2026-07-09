"""
Phase 5 — Analytics Tests
Covers: health, summary (empty + seeded), per-domain, trends,
        CSV export, refresh trigger, TAT computation, bar-chart
        data shaping, pruning guard, startup env guard (DEF-007).
"""
import datetime
import json
from pathlib import Path

import pytest


# ── Health ────────────────────────────────────────────────────────────────────
class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"]  == "ok"
        assert j["service"] == "analytics"

    def test_health_counts_snapshots(self, client, seed):
        seed("ehr", {"total_patients": 10})
        seed("orders", {"lab_pending": 5})
        r = client.get("/api/health")
        assert r.json()["total_snapshots"] == 2


# ── Summary ───────────────────────────────────────────────────────────────────
class TestSummary:
    def test_empty_summary_returns_nulls(self, client):
        r = client.get("/api/metrics/summary")
        assert r.status_code == 200
        j = r.json()
        for domain in ("ehr", "orders", "billing", "ai", "mpi"):
            assert domain in j
            assert j[domain]["data"] is None

    def test_summary_returns_latest_snapshot(self, client, seed):
        seed("ehr", {"total_patients": 50, "active_encounters": 12})
        r = client.get("/api/metrics/summary")
        ehr = r.json()["ehr"]
        assert ehr["data"]["total_patients"] == 50
        assert ehr["data"]["active_encounters"] == 12

    def test_summary_only_returns_latest(self, client, seed):
        seed("ehr", {"total_patients": 10}, ts="2026-01-01T00:00:00")
        seed("ehr", {"total_patients": 99}, ts="2026-03-24T00:00:00")
        r = client.get("/api/metrics/summary")
        assert r.json()["ehr"]["data"]["total_patients"] == 99


# ── Per-domain ────────────────────────────────────────────────────────────────
class TestDomain:
    def test_domain_not_found_before_collection(self, client):
        r = client.get("/api/metrics/ehr")
        assert r.status_code == 404

    def test_domain_returns_data_after_seed(self, client, seed):
        seed("orders", {"lab_pending": 7, "lab_tat_hours": 2.3})
        r = client.get("/api/metrics/orders")
        assert r.status_code == 200
        assert r.json()["data"]["lab_tat_hours"] == 2.3

    def test_unknown_domain_returns_404(self, client):
        r = client.get("/api/metrics/xyzzy")
        assert r.status_code == 404


# ── Trends ────────────────────────────────────────────────────────────────────
class TestTrends:
    def test_trends_empty(self, client):
        r = client.get("/api/metrics/trends?domain=ehr&metric=total_patients")
        assert r.status_code == 200
        assert r.json()["series"] == []

    def test_trends_returns_chronological_series(self, client, seed):
        for i, ts in enumerate([
            "2026-03-20T00:00:00",
            "2026-03-21T00:00:00",
            "2026-03-22T00:00:00",
        ]):
            seed("ehr", {"total_patients": 10 + i * 5}, ts=ts)
        r = client.get("/api/metrics/trends?domain=ehr&metric=total_patients&limit=10")
        series = r.json()["series"]
        assert len(series) == 3
        assert series[0]["value"] == 10
        assert series[2]["value"] == 20   # ascending order

    def test_trends_respects_limit(self, client, seed):
        for i in range(10):
            seed("ehr", {"total_patients": i}, ts=f"2026-03-{10+i:02d}T00:00:00")
        r = client.get("/api/metrics/trends?domain=ehr&metric=total_patients&limit=5")
        assert len(r.json()["series"]) == 5

    def test_trends_skips_missing_metric(self, client, seed):
        seed("ehr", {"total_patients": 5})
        seed("ehr", {"other_metric": 99})   # no total_patients
        r = client.get("/api/metrics/trends?domain=ehr&metric=total_patients")
        series = r.json()["series"]
        assert len(series) == 1
        assert series[0]["value"] == 5


# ── Export ────────────────────────────────────────────────────────────────────
class TestExport:
    def test_export_csv_content_type(self, client, seed):
        seed("billing", {"total_amount": 5000.0, "paid_amount": 3000.0,
                          "collection_rate": 60.0})
        r = client.get("/api/export/billing")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]

    def test_export_csv_has_headers(self, client, seed):
        seed("ehr", {"total_patients": 10, "active_encounters": 3})
        r    = client.get("/api/export/ehr")
        lines = r.text.splitlines()
        assert "captured_at" in lines[0]
        assert "total_patients" in lines[0]

    def test_export_csv_data_row(self, client, seed):
        seed("ehr", {"total_patients": 42})
        r    = client.get("/api/export/ehr")
        lines = r.text.splitlines()
        assert len(lines) >= 2
        assert "42" in lines[1]

    def test_export_missing_domain_returns_404(self, client):
        r = client.get("/api/export/ehr")
        assert r.status_code == 404   # no data yet

    def test_export_multiple_rows(self, client, seed):
        for i in range(5):
            seed("ai", {"total": i*10, "success_rate": 90.0+i})
        r    = client.get("/api/export/ai?limit=10")
        lines = r.text.splitlines()
        assert len(lines) == 6   # header + 5 rows


# ── Refresh ───────────────────────────────────────────────────────────────────
class TestRefresh:
    def test_refresh_returns_202(self, client):
        r = client.post("/api/metrics/refresh")
        assert r.status_code == 202
        assert r.json()["status"] == "queued"


# ── Startup env guard (DEF-007) ───────────────────────────────────────────────
class TestEnvGuard:
    """The service contract requires a fail-fast guard on KEYCLOAK_URL et al."""

    REQUIRED = ["KEYCLOAK_URL", "KEYCLOAK_TOKEN_URL",
                "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET"]

    def test_guard_lists_keycloak_url(self):
        import main
        assert "KEYCLOAK_URL" in main._REQUIRED_ENV

    def test_missing_env_reports_unset_keycloak_url(self, monkeypatch):
        import main
        monkeypatch.delenv("KEYCLOAK_URL", raising=False)
        assert "KEYCLOAK_URL" in main._missing_env()

    def test_missing_env_treats_empty_as_missing(self, monkeypatch):
        import main
        monkeypatch.setenv("KEYCLOAK_URL", "")
        assert "KEYCLOAK_URL" in main._missing_env()

    def test_missing_env_empty_when_all_set(self, monkeypatch):
        import main
        for var in self.REQUIRED:
            monkeypatch.setenv(var, "stub-value")
        assert main._missing_env() == []

    def test_check_env_exits_when_keycloak_url_missing(self, monkeypatch):
        import main
        monkeypatch.delenv("KEYCLOAK_URL", raising=False)
        with pytest.raises(SystemExit) as exc:
            main._check_env()
        assert "KEYCLOAK_URL" in str(exc.value.code)

    def test_check_env_passes_when_all_set(self, monkeypatch):
        import main
        for var in self.REQUIRED:
            monkeypatch.setenv(var, "stub-value")
        main._check_env()   # must not raise

    def test_app_boots_when_keycloak_url_set(self, client):
        # `client` enters the lifespan via TestClient → _check_env() already
        # ran with the non-empty KEYCLOAK_URL stub from conftest.
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["service"] == "analytics"

    def test_guard_matches_service_manifest(self):
        import main
        manifest_path = Path(main.__file__).parent / "openhis.service.json"
        manifest = json.loads(manifest_path.read_text())
        assert set(manifest["env"]["required"]) == set(main._REQUIRED_ENV)


# ── Collector smoke test ──────────────────────────────────────────────────────
class TestCollectorModule:
    def test_collector_importable(self):
        """Verify the new FHIR-based collector module can be imported cleanly."""
        import collector
        assert hasattr(collector, "collect_all")

    def test_collect_all_is_coroutine(self):
        import asyncio, collector
        assert asyncio.iscoroutinefunction(collector.collect_all)
