"""
Scenario 5 — Admin Plane & Observability

Mirrors SCENARIO 5 in docs/verification_and_validation/v-and-v-scenario.md.

Covers:
  ✅ S5.1  — service registry lists every self-registered core service
  ✅ S5.2  — /api/services returns live health (online/offline) for each
  ✅ S5.3  — /api/audit is reachable and returns a list
  ✅ S5.4  — /api/events/recent is reachable and returns a list
  ✅ S5.5  — /api/profiles/active shape
  ✅ S5.6  — /api/config list shape
  ✅ S5.7  — /api/announcements list shape
  ✅ S5.8  — /api/platform/topology nodes ⊇ core services
  ✅ S5.9  — admin SPA loads
  ✅ S5.10 — /api/config GET single key (200 + 404)
  ✅ S5.11 — /api/config PUT round-trip (write → re-read same value)
  ✅ S5.12 — /api/config PUT validation (missing value → 400)
  ✅ S5.13 — /api/announcements POST → GET → PATCH → DELETE
  ✅ S5.14 — /api/announcements POST validation (title/body/severity)
  ✅ S5.15 — /api/platform/profiles lists every known profile with shape
  ✅ S5.16 — /api/platform/ram returns active_profiles + total_mb ≥ base
"""
import uuid

import pytest


pytestmark = pytest.mark.e2e


E2E_CONFIG_PREFIX       = "e2e."
E2E_ANNOUNCEMENT_PREFIX = "[E2E] "
KNOWN_PROFILES = {"emr", "laboratory", "erp", "imaging", "analytics"}


# Services expected to self-register in the admin registry.
# Non-base profiles (ris, analytics, ai-controller) don't self-register in the
# current admin v2 design, so we only assert on the base profile.
CORE_REGISTERED = {"admin", "mpi", "hl7", "integration-hub"}


class TestS5_AdminRegistry:

    def test_s5_1_registry_lists_core_services(self, admin_api):
        r = admin_api.get("/registry")
        assert r.status_code == 200
        body = r.json()
        assert "services" in body
        names = {s["name"] for s in body["services"]}
        missing = CORE_REGISTERED - names
        assert not missing, f"registry missing core services: {missing}"

    def test_s5_2_services_health_view(self, admin_api):
        r = admin_api.get("/services")
        assert r.status_code == 200
        body = r.json()
        assert "services" in body
        for svc in body["services"]:
            assert svc.get("name")
            assert svc.get("status") in ("online", "offline", "degraded")
            assert isinstance(svc.get("http_status", 0), int)

    def test_s5_3_audit_log_reachable(self, admin_api):
        r = admin_api.get("/audit")
        assert r.status_code == 200
        # Admin audit is a list (possibly empty in the current build — see DEF-002).
        assert isinstance(r.json(), list)

    def test_s5_4_event_stream_recent(self, admin_api):
        r = admin_api.get("/events/recent", params={"limit": 50})
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        # Each entry, if any, must carry the documented fields (even if empty str).
        for ev in r.json():
            for key in ("id", "type", "source", "payload", "ts"):
                assert key in ev, f"event missing key {key}: {ev}"

    def test_s5_5_profiles_active(self, admin_api):
        r = admin_api.get("/profiles/active")
        assert r.status_code == 200
        body = r.json()
        assert "profiles" in body
        assert isinstance(body["profiles"], list)

    def test_s5_6_config_kv_store(self, admin_api):
        r = admin_api.get("/config")
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        # Each row has the documented shape if non-empty.
        for row in rows:
            assert "key" in row
            assert "value" in row

    def test_s5_7_announcements_reachable(self, admin_api):
        r = admin_api.get("/announcements")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_s5_8_platform_topology(self, admin_api):
        """Topology graph: one node per registered service; edges optional."""
        r = admin_api.get("/platform/topology")
        assert r.status_code == 200
        body = r.json()
        assert "nodes" in body
        names = {n["id"] for n in body["nodes"]}
        assert CORE_REGISTERED.issubset(names)


class TestS5_AdminDashboardSPA:
    """The admin SPA must be reachable (the portal card is the entrypoint for
    every operator — if the HTML doesn't load there is no other way in)."""

    def test_s5_9_admin_spa_loads(self, http):
        r = http.get("/admin/")
        assert r.status_code == 200
        assert "Admin Dashboard" in r.text or "admin" in r.text.lower()


class TestS5_ConfigKeyValueWrite:
    """
    The /api/config router has been at zero coverage — every operator action
    that tunes the platform (feature flags, retention windows, banner text,
    …) goes through PUT /api/config/{key}. Walk the full read/write loop so
    a regression in the SQLite upsert path or the audit hook trips a test
    instead of a silent prod surprise.
    """

    def test_s5_10_config_get_single_key(self, admin_api):
        r = admin_api.get(f"/config/{uuid.uuid4().hex}")
        assert r.status_code == 404, r.text

        rows = admin_api.get("/config").json()
        if rows:
            existing_key = rows[0]["key"]
            r2 = admin_api.get(f"/config/{existing_key}")
            assert r2.status_code == 200
            assert r2.json()["key"] == existing_key

    def test_s5_11_config_put_round_trip(self, admin_api, request):
        key = f"{E2E_CONFIG_PREFIX}{uuid.uuid4().hex[:8]}"
        value = f"e2e-{uuid.uuid4().hex[:6]}"
        r = admin_api.put(f"/config/{key}", json={"value": value})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["key"]   == key
        assert body["value"] == value
        assert body.get("updated_at")
        assert body.get("updated_by")

        # Re-read both via list and via single-key GET — both must agree.
        r2 = admin_api.get(f"/config/{key}")
        assert r2.status_code == 200
        assert r2.json()["value"] == value

        all_rows = admin_api.get("/config").json()
        assert any(row["key"] == key and row["value"] == value for row in all_rows)
        request.config.cache.set("s5/config_key", key)

    def test_s5_12_config_put_requires_value(self, admin_api):
        r = admin_api.put(f"/config/{E2E_CONFIG_PREFIX}_no_value", json={})
        assert r.status_code == 400, r.text


class TestS5_AnnouncementsLifecycle:
    """
    /api/announcements is how the admin pushes platform-wide banners (incident
    notice, planned maintenance, etc). Cover the create → list → update →
    soft-deactivate → delete loop so the dependency on the local SQLite
    schema and the audit hook can't silently break.
    """

    def test_s5_13_announcement_lifecycle(self, admin_api, request):
        title = f"{E2E_ANNOUNCEMENT_PREFIX}{uuid.uuid4().hex[:8]}"
        body  = "Scheduled maintenance window — e2e probe."
        r = admin_api.post("/announcements", json={
            "title":    title,
            "body":     body,
            "severity": "warning",
        })
        assert r.status_code == 201, r.text
        created = r.json()
        ann_id = created["id"]
        assert created["title"]    == title
        assert created["severity"] == "warning"
        assert created.get("active") in (1, True)

        # Default list (active_only=True) must include the new row.
        active = admin_api.get("/announcements").json()
        assert any(a["id"] == ann_id for a in active)

        # PATCH — change severity then deactivate.
        r2 = admin_api.patch(f"/announcements/{ann_id}", json={"severity": "info"})
        assert r2.status_code == 200
        assert r2.json()["severity"] == "info"

        r3 = admin_api.patch(f"/announcements/{ann_id}", json={"active": 0})
        assert r3.status_code == 200
        assert r3.json().get("active") in (0, False)

        # Default list now omits the deactivated entry; full list still has it.
        active = admin_api.get("/announcements").json()
        assert not any(a["id"] == ann_id for a in active)
        all_rows = admin_api.get("/announcements", params={"active_only": "false"}).json()
        assert any(a["id"] == ann_id for a in all_rows)

        # DELETE — and the row is gone from both views.
        r4 = admin_api.delete(f"/announcements/{ann_id}")
        assert r4.status_code == 204, r4.text
        all_rows = admin_api.get("/announcements", params={"active_only": "false"}).json()
        assert not any(a["id"] == ann_id for a in all_rows)
        # 404 on a follow-up DELETE proves the row is really gone.
        r5 = admin_api.delete(f"/announcements/{ann_id}")
        assert r5.status_code == 404

    def test_s5_14_announcement_validation(self, admin_api):
        r = admin_api.post("/announcements", json={"title": "", "body": "x"})
        assert r.status_code == 400

        r = admin_api.post("/announcements", json={"title": "x", "body": ""})
        assert r.status_code == 400

        r = admin_api.post("/announcements", json={
            "title": f"{E2E_ANNOUNCEMENT_PREFIX}bad-sev",
            "body":  "x",
            "severity": "catastrophic",
        })
        assert r.status_code == 422


class TestS5_PlatformProfilesAndRam:
    """
    /api/platform/profiles + /api/platform/ram drive the admin "Profiles"
    panel. Both walk the compose YAML metadata and the RAM table, so a
    schema or path regression here breaks the operator's only RAM-budget
    view. Topology already has S5.8 — this fills the rest of the router.
    """

    def test_s5_15_platform_profiles_shape(self, admin_api):
        r = admin_api.get("/platform/profiles")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list) and rows
        names = {row["name"] for row in rows}
        assert names == KNOWN_PROFILES, f"unexpected profiles: {names ^ KNOWN_PROFILES}"
        for row in rows:
            assert isinstance(row["active"], bool)
            assert isinstance(row["ram_mb"], int) and row["ram_mb"] > 0
            assert isinstance(row.get("requires", []),    list)
            assert isinstance(row.get("integrates", []),  list)
            assert isinstance(row.get("nginx_routes", []),list)

    def test_s5_16_platform_ram_estimate(self, admin_api):
        r = admin_api.get("/platform/ram")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["active_profiles"], list)
        assert isinstance(body["total_mb"], int)
        # Base stack must always be counted (≥ 512 MB by definition).
        assert body["total_mb"] >= 512
        assert body["total_gb"] == round(body["total_mb"] / 1024, 1)
