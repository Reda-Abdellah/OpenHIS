"""
Scenario 8 — Analytics Service

Added 2026-04-19 after the portal walkthrough exposed DEF-007: every analytics
feature endpoint returns 503 "Identity provider not configured (KEYCLOAK_URL
missing)" because the analytics container is missing required env vars.

When DEF-007 is fixed these tests flip from xfail → PASS automatically.

Covers:
  ✅ S8.1 — analytics SPA loads
  ✅ S8.2 — /analytics/api/health returns ok (misleadingly green — see DEF-007)
  ❌ S8.3 — /api/metrics/summary returns KPI object (DEF-007)
  ❌ S8.4 — /api/metrics/trends returns time series (DEF-007)
  ❌ S8.5 — /api/metrics/refresh accepts a snapshot request (DEF-007)
  ❌ S8.6 — /api/export/{domain} returns CSV (DEF-007)
"""
import pytest


pytestmark = pytest.mark.e2e


class TestS8_AnalyticsUI:

    def test_s8_1_spa_loads(self, http):
        r = http.get("/analytics/")
        assert r.status_code == 200
        assert "Analytics" in r.text

    def test_s8_2_health_reports_ok(self, analytics_api):
        """
        NOTE: /api/health returns ok even when the service cannot serve any
        feature call — DEF-007 says this should fail at startup, not silently
        accept health probes while rejecting real requests.
        """
        r = analytics_api.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestS8_AnalyticsFeatures:

    @pytest.mark.xfail(
        reason="DEF-007: analytics container runs without KEYCLOAK_URL, so "
               "every feature route returns 503 before validating the token.",
        strict=False,
    )
    def test_s8_3_metrics_summary(self, analytics_api):
        r = analytics_api.get("/metrics/summary")
        assert r.status_code == 200
        body = r.json()
        for domain in ("patients", "lab", "imaging"):
            assert domain in body

    @pytest.mark.xfail(reason="DEF-007", strict=False)
    def test_s8_4_metrics_trends(self, analytics_api):
        r = analytics_api.get("/metrics/trends")
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))

    @pytest.mark.xfail(reason="DEF-007", strict=False)
    def test_s8_5_metrics_refresh(self, analytics_api):
        r = analytics_api.post("/metrics/refresh")
        assert r.status_code in (200, 202)

    @pytest.mark.xfail(reason="DEF-007", strict=False)
    def test_s8_6_export_patients(self, analytics_api):
        r = analytics_api.get("/export/patients")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith(("text/csv", "application/csv"))
