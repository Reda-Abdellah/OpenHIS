"""
Integration: imaging order cross-service flow.

Flow: EHR creates IMAGING order
       → fires POST /api/events/imaging-order to FHIR bridge
         → FHIR bridge POSTs /api/orders to RIS
         → FHIR bridge PATCHes /api/orders/{id} back in EHR with accession number
"""
import respx, httpx

RIS_BASE  = "http://ris:8002/api"
EHR_BASE  = "http://ehr:8003/api"
FHIR_BASE = "http://fhir-bridge:8005"


# ── Phase 1: EHR fires imaging-order event ─────────────────────────────────

class TestEHRFiresImagingOrderEvent:

    def test_imaging_order_calls_fhir_bridge(self, ehr_client, ehr_patient):
        captured = {}

        def capture(req):
            import json
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"status": "queued"})

        pid = ehr_patient["id"]
        with respx.mock:
            respx.post(f"{FHIR_BASE}/api/events/imaging-order").mock(
                side_effect=capture
            )
            r = ehr_client.post("/api/orders", json={
                "order_type": "IMAGING",
                "patient_id": pid,
                "requesting_physician": "Dr. House",
                "order_detail": {"modality": "CT", "bodypart": "CHEST"},
                "priority": "ROUTINE"
            })

        assert r.status_code == 201, r.text
        assert captured, "FHIR bridge was not called for IMAGING order"
        body = captured["body"]
        assert body["order_type"] == "IMAGING"
        assert body["patient_id"] == pid

    def test_lab_order_calls_fhir_bridge(self, ehr_client, ehr_patient):
        captured = {}

        def capture(req):
            import json
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"status": "queued"})

        with respx.mock:
            respx.post(f"{FHIR_BASE}/api/events/lab-order").mock(
                side_effect=capture
            )
            r = ehr_client.post("/api/orders", json={
                "order_type": "LAB",
                "patient_id": ehr_patient["id"],
                "order_detail": {"test_code": "CBC"},
            })

        assert r.status_code == 201
        assert captured, "FHIR bridge was not called for LAB order"

    def test_pharmacy_order_does_not_fire_lab_or_imaging_event(
        self, ehr_client, ehr_patient
    ):
        """PHARMACY orders must NOT trigger imaging-order or lab-order events."""
        with respx.mock:
            # Register the valid pharmacy route so respx doesn't fail on it
            respx.post(f"{FHIR_BASE}/api/events/pharmacy-order").mock(
                return_value=httpx.Response(200, json={"status": "queued"})
            )
            r = ehr_client.post("/api/orders", json={
                "order_type": "PHARMACY",
                "patient_id": ehr_patient["id"],
            })
        assert r.status_code == 201

    def test_unsupported_order_type_rejected(self, ehr_client, ehr_patient):
        r = ehr_client.post("/api/orders", json={
            "order_type": "XRAY",  # not in allowed types
            "patient_id": ehr_patient["id"],
        })
        assert r.status_code == 422


# ── Phase 2: FHIR bridge routes imaging-order to RIS ──────────────────────

class TestFHIRBridgeRoutesImagingOrderToRIS:

    IMAGING_ORDER = {
        "id": 42, "patient_id": "P-001", "order_type": "IMAGING",
        "priority": "ROUTINE", "requesting_physician": "Dr. House",
        "order_detail": {"modality": "CT", "bodypart": "CHEST",
                         "clinical_info": "Pulmonary nodule follow-up"},
        "mrn": "INT001"
    }

    def test_imaging_order_calls_ris(self, fhir_client):
        captured = {}

        def capture_ris(req):
            import json
            captured["body"] = json.loads(req.content)
            return httpx.Response(201, json={
                "id": 1, "accession_number": "ACC-20260325-1234"
            })

        with respx.mock:
            respx.post(f"{RIS_BASE}/orders").mock(side_effect=capture_ris)
            respx.route(method="PATCH", url__startswith=f"{EHR_BASE}/orders/").mock(
                return_value=httpx.Response(200, json={"id": 42})
            )
            r = fhir_client.post("/api/events/imaging-order",
                                 json=self.IMAGING_ORDER)

        assert r.status_code in (200, 202)
        assert captured, "RIS was not called for imaging order"
        body = captured["body"]
        assert body.get("modality") == "CT"
        assert body.get("priority") == "ROUTINE"

    def test_imaging_order_ris_payload_has_required_fields(self, fhir_client):
        """RIS order creation needs at least modality and patient_id."""
        captured = {}

        with respx.mock:
            respx.post(f"{RIS_BASE}/orders").mock(
                side_effect=lambda req: (
                    captured.update({"body": __import__("json").loads(req.content)})
                    or httpx.Response(201, json={
                        "id": 1, "accession_number": "ACC-20260325-9999"
                    })
                )
            )
            respx.route(method="PATCH", url__startswith=f"{EHR_BASE}/orders/").mock(
                return_value=httpx.Response(200, json={"id": 42})
            )
            fhir_client.post("/api/events/imaging-order", json=self.IMAGING_ORDER)

        body = captured["body"]
        assert "modality" in body
        assert "patient_id" in body
        assert "priority" in body

    def test_ris_accession_written_back_to_ehr(self, fhir_client):
        """After RIS creates the order, its accession number must be PATCHed back."""
        ehr_patch_calls = []

        def capture_patch(req):
            import json
            ehr_patch_calls.append({
                "url": str(req.url),
                "body": json.loads(req.content)
            })
            return httpx.Response(200, json={"id": 42})

        with respx.mock:
            respx.post(f"{RIS_BASE}/orders").mock(
                return_value=httpx.Response(201, json={
                    "id": 1, "accession_number": "ACC-20260325-5678"
                })
            )
            respx.route(method="PATCH", url__startswith=f"{EHR_BASE}/orders/").mock(
                side_effect=capture_patch
            )
            fhir_client.post("/api/events/imaging-order", json=self.IMAGING_ORDER)

        assert ehr_patch_calls, "EHR order write-back was not called"
        patch = ehr_patch_calls[0]
        assert "42" in patch["url"]  # should PATCH order id 42
        body = patch["body"]
        assert body.get("ehr_order_id") == "ACC-20260325-5678"
        assert body.get("status") == "SENT"

    def test_ris_failure_does_not_crash_event_handler(self, fhir_client):
        """If RIS is down, the event handler should return 200 (queued), not 500."""
        with respx.mock:
            respx.post(f"{RIS_BASE}/orders").mock(
                return_value=httpx.Response(503, text="RIS unavailable")
            )
            r = fhir_client.post("/api/events/imaging-order",
                                 json=self.IMAGING_ORDER)
        assert r.status_code in (200, 202)


# ── Phase 3: RIS processes the order correctly ─────────────────────────────

class TestRISAcceptsImagingOrder:
    """Validate that RIS handles incoming orders from FHIR bridge correctly."""

    def _setup_patient(self, ris_client):
        r = ris_client.post("/api/patients", json={
            "mrn": "INT001", "patient_name": "Test Patient"
        })
        assert r.status_code == 201
        return r.json()["id"]

    def test_ris_creates_order_with_accession(self, ris_client):
        pid = self._setup_patient(ris_client)
        r = ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CT",
            "body_part": "CHEST", "priority": "ROUTINE",
        })
        assert r.status_code == 201
        order = r.json()
        assert order["accession_number"].startswith("ACC-")
        assert order["status"] == "PENDING"
        assert order["modality"] == "CT"

    def test_ris_order_appears_in_worklist(self, ris_client):
        pid = self._setup_patient(ris_client)
        ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "MR", "priority": "STAT"
        })
        r = ris_client.get("/api/worklist?modality=MR")
        assert r.status_code == 200
        items = r.json()
        assert any(o["modality"] == "MR" for o in items)

    def test_stat_priority_appears_first_in_worklist(self, ris_client):
        """STAT orders must be ordered before ROUTINE in the worklist."""
        pid = self._setup_patient(ris_client)
        ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CR", "priority": "ROUTINE"
        })
        ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CR", "priority": "STAT"
        })
        r = ris_client.get("/api/worklist")
        assert r.status_code == 200
        orders = r.json()
        priorities = [o["priority"] for o in orders]
        stat_idx   = priorities.index("STAT")
        routine_idx = priorities.index("ROUTINE")
        assert stat_idx < routine_idx, "STAT should precede ROUTINE in worklist"
