"""
Integration: lab order + result cross-service flow.

Flow 1: EHR creates LAB order
         → FHIR bridge POST /api/events/lab-order
           → FHIR bridge POSTs /api/specimens to LIS
           → FHIR bridge POSTs /api/lab-orders to LIS
           → FHIR bridge PATCHes EHR order with LIS reference

Flow 2: LIS finalises a result
         → FHIR bridge POST /api/events/lab-result-final
           → FHIR bridge POSTs /api/orders/from-lis-result to EHR (CDSS trigger)
"""
import respx, httpx

LIS_BASE = "http://lis:8004/api"
EHR_BASE = "http://ehr:8003/api"


# ── Phase 1: FHIR bridge routes lab-order to LIS ──────────────────────────

class TestFHIRBridgeRoutesLabOrderToLIS:

    LAB_ORDER = {
        "id": 55, "patient_id": "P-001", "order_type": "LAB",
        "requesting_physician": "Dr. Watson",
        "priority": "ROUTINE",
        "order_detail": {"test_code": "CBC", "specimen_type": "blood"},
        "mrn": "INT001",
    }

    def _lis_patient_exists(self):
        """Returns a respx route that simulates LIS having the patient."""
        return respx.route(method="GET", url__startswith=f"{LIS_BASE}/lab-patients").mock(
            return_value=httpx.Response(200, json=[{"id": 7, "mrn": "INT001"}])
        )

    def test_lab_order_creates_lis_specimen(self, fhir_client):
        captured = {}

        def capture_spec(req):
            import json
            captured["specimen"] = json.loads(req.content)
            return httpx.Response(201, json={"id": 3})

        with respx.mock:
            self._lis_patient_exists()
            respx.post(f"{LIS_BASE}/specimens").mock(side_effect=capture_spec)
            respx.post(f"{LIS_BASE}/lab-orders").mock(
                return_value=httpx.Response(201, json={"id": 8})
            )
            respx.route(method="PATCH", url__startswith=f"{EHR_BASE}/orders/").mock(
                return_value=httpx.Response(200, json={})
            )

            r = fhir_client.post("/api/events/lab-order", json=self.LAB_ORDER)

        assert r.status_code in (200, 202)
        assert captured.get("specimen"), "LIS specimen was not created"
        spec = captured["specimen"]
        assert spec.get("patient_id") == 7
        assert spec.get("specimen_type") == "blood"

    def test_lab_order_creates_lis_order(self, fhir_client):
        captured = {}

        def capture_order(req):
            import json
            captured["order"] = json.loads(req.content)
            return httpx.Response(201, json={"id": 8})

        with respx.mock:
            self._lis_patient_exists()
            respx.post(f"{LIS_BASE}/specimens").mock(
                return_value=httpx.Response(201, json={"id": 3})
            )
            respx.post(f"{LIS_BASE}/lab-orders").mock(side_effect=capture_order)
            respx.route(method="PATCH", url__startswith=f"{EHR_BASE}/orders/").mock(
                return_value=httpx.Response(200, json={})
            )

            fhir_client.post("/api/events/lab-order", json=self.LAB_ORDER)

        assert captured.get("order"), "LIS lab-order was not created"
        order = captured["order"]
        assert order.get("ehr_order_id") == "55"
        assert order.get("test_code") == "CBC"
        assert order.get("specimen_id") == 3

    def test_lab_order_ehr_writeback_after_lis_order(self, fhir_client):
        """EHR order must be PATCHed with LIS order reference after creation."""
        patch_calls = []

        def capture_patch(req):
            import json
            patch_calls.append({
                "url": str(req.url),
                "body": json.loads(req.content)
            })
            return httpx.Response(200, json={})

        with respx.mock:
            self._lis_patient_exists()
            respx.post(f"{LIS_BASE}/specimens").mock(
                return_value=httpx.Response(201, json={"id": 3})
            )
            respx.post(f"{LIS_BASE}/lab-orders").mock(
                return_value=httpx.Response(201, json={"id": 8})
            )
            respx.route(method="PATCH", url__startswith=f"{EHR_BASE}/orders/").mock(
                side_effect=capture_patch
            )

            fhir_client.post("/api/events/lab-order", json=self.LAB_ORDER)

        assert patch_calls, "EHR writeback not called after LIS order"
        p = patch_calls[0]
        assert "55" in p["url"]
        assert p["body"].get("status") == "SENT"
        assert "LIS-8" in p["body"].get("external_ref", "")

    def test_lab_order_skipped_when_patient_not_in_lis(self, fhir_client):
        """If patient not found in LIS, lab-order should still return 200."""
        with respx.mock:
            respx.route(method="GET", url__startswith=f"{LIS_BASE}/lab-patients").mock(
                return_value=httpx.Response(200, json=[])
            )

            r = fhir_client.post("/api/events/lab-order", json=self.LAB_ORDER)

        assert r.status_code in (200, 202), "Event handler must not crash on missing LIS patient"


# ── Phase 2: LIS finalizes result → EHR CDSS ──────────────────────────────

class TestFHIRBridgeLabResultTriggersCDSS:

    LAB_RESULT = {
        "order_id": 8,
        "ehr_patient_id": "P-001",
        "test_code": "HBA1C",
        "results": [
            {"code": "HBA1C", "value": "8.5", "unit": "%",
             "reference_range": "4.0-5.6", "flag": "H"}
        ],
        "status": "FINAL"
    }

    def test_lab_result_final_notifies_ehr_cdss(self, fhir_client):
        captured = {}

        def capture(req):
            import json
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"status": "ok", "alerts_created": 1})

        with respx.mock:
            respx.post(f"{EHR_BASE}/orders/from-lis-result").mock(
                side_effect=capture
            )
            r = fhir_client.post("/api/events/lab-result-final",
                                 json=self.LAB_RESULT)

        assert r.status_code in (200, 202)
        assert captured, "EHR CDSS endpoint was not called"
        body = captured["body"]
        assert body.get("ehr_patient_id") == "P-001"

    def test_lab_result_final_event_returns_ok_even_if_ehr_down(self, fhir_client):
        with respx.mock:
            respx.post(f"{EHR_BASE}/orders/from-lis-result").mock(
                return_value=httpx.Response(503, text="EHR down")
            )
            r = fhir_client.post("/api/events/lab-result-final",
                                 json=self.LAB_RESULT)
        assert r.status_code in (200, 202), "Bridge must not surface downstream failures"


# ── Phase 3: EHR CDSS processes incoming lab results ──────────────────────

class TestEHRCDSSProcessesLabResult:
    """EHR /api/orders/from-lis-result must create CDSS alerts for abnormal results."""

    def _setup(self, ehr_client):
        """Create a patient, encounter, and order."""
        import respx, httpx
        with respx.mock:
            respx.post("http://fhir-bridge:8005/api/events/patient-created").mock(
                return_value=httpx.Response(200, json={})
            )
            pt = ehr_client.post("/api/patients", json={
                "mrn": "CDSS001", "first_name": "Lab", "last_name": "Patient",
            }).json()
        return pt

    def test_normal_lab_result_creates_no_alert(self, ehr_client):
        pt = self._setup(ehr_client)
        r = ehr_client.post("/api/orders/from-lis-result", json={
            "ehr_patient_id": pt["id"],
            "order_id": 1,
            "results": [
                {"code": "WBC", "value": "7.0", "unit": "K/uL",
                 "reference_range": "4.5-11.0", "flag": ""}
            ],
            "status": "FINAL"
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("alerts_created", 0) == 0

    def test_missing_ehr_patient_id_is_skipped(self, ehr_client):
        """Without ehr_patient_id, the endpoint should skip gracefully."""
        r = ehr_client.post("/api/orders/from-lis-result", json={
            "order_id": 99,
            "results": [],
            "status": "FINAL"
        })
        assert r.status_code == 200
        assert r.json().get("status") == "skipped"
