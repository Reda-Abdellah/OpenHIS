"""Integration Hub — audit log endpoint tests."""


class TestAuditEndpoint:
    def test_audit_returns_200(self, client):
        r = client.get("/api/audit")
        assert r.status_code == 200

    def test_audit_has_required_fields(self, client):
        r = client.get("/api/audit")
        j = r.json()
        assert "count"             in j
        assert "events"            in j
        assert "retry_queue_depth" in j

    def test_audit_empty_on_fresh_start(self, client):
        r = client.get("/api/audit")
        assert r.json()["count"] == 0
        assert r.json()["events"] == []

    def test_audit_pagination_params_accepted(self, client):
        r = client.get("/api/audit?limit=10&offset=0")
        assert r.status_code == 200

    def test_audit_filter_by_event_type(self, client):
        r = client.get("/api/audit?event_type=patient_synced")
        assert r.status_code == 200

    def test_audit_filter_by_resource_type(self, client):
        r = client.get("/api/audit?resource_type=Patient")
        assert r.status_code == 200

    def test_audit_limit_out_of_range_rejected(self, client):
        r = client.get("/api/audit?limit=999")
        assert r.status_code == 422   # FastAPI validation

    def test_audit_negative_offset_rejected(self, client):
        r = client.get("/api/audit?offset=-1")
        assert r.status_code == 422
