"""
Phase 5 — Analytics Tests
Covers: health, summary (empty + seeded), per-domain, trends,
        CSV export, refresh trigger, TAT computation, bar-chart
        data shaping, pruning guard.
"""
import datetime


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


# ── Collector unit tests (no HTTP needed) ─────────────────────────────────────
class TestCollectorLogic:
    def test_tat_hours_completed_only(self):
        from collector import _tat_hours
        items = [
            {"status": "COMPLETED", "createdat": "2026-01-01T08:00:00",
             "updatedat": "2026-01-01T10:00:00"},
            {"status": "PENDING",   "createdat": "2026-01-01T08:00:00",
             "updatedat": "2026-01-01T09:00:00"},
        ]
        result = _tat_hours(items)
        assert result == 2.0

    def test_tat_hours_empty_list(self):
        from collector import _tat_hours
        assert _tat_hours([]) is None

    def test_tat_hours_ignores_zero_duration(self):
        from collector import _tat_hours
        items = [
            {"status": "COMPLETED", "createdat": "2026-01-01T10:00:00",
             "updatedat": "2026-01-01T10:00:00"},   # 0 hours — excluded
        ]
        assert _tat_hours(items) is None

    def test_by_status_counts(self):
        from collector import _by_status
        items = [{"status": "A"}, {"status": "B"},
                 {"status": "A"}, {"status": "A"}]
        result = _by_status(items)
        assert result == {"A": 3, "B": 1}

    def test_by_status_empty(self):
        from collector import _by_status
        assert _by_status([]) == {}

    def test_by_status_missing_field_uses_unknown(self):
        from collector import _by_status
        items = [{"status": "OK"}, {"nope": "X"}]
        result = _by_status(items)
        assert result.get("UNKNOWN") == 1

    def test_tat_hours_multiple_orders_averaged(self):
        from collector import _tat_hours
        items = [
            {"status": "COMPLETED", "createdat": "2026-01-01T08:00:00",
             "updatedat": "2026-01-01T10:00:00"},   # 2h
            {"status": "COMPLETED", "createdat": "2026-01-01T08:00:00",
             "updatedat": "2026-01-01T12:00:00"},   # 4h
        ]
        result = _tat_hours(items)
        assert result == 3.0

    def test_tat_hours_caps_unreasonable_values(self):
        from collector import _tat_hours
        items = [
            {"status": "COMPLETED", "createdat": "2020-01-01T00:00:00",
             "updatedat": "2026-01-01T00:00:00"},   # >720h — excluded
            {"status": "COMPLETED", "createdat": "2026-01-01T08:00:00",
             "updatedat": "2026-01-01T09:00:00"},   # 1h — valid
        ]
        assert _tat_hours(items) == 1.0
