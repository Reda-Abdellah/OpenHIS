"""
Integration: lab order + result cross-service flow (new architecture).

Flow:
  1. OpenMRS has a ServiceRequest (lab order)
     → Hub polls OpenMRS → creates it in OpenELIS
  2. OpenELIS finalises a DiagnosticReport
     → Hub polls OpenELIS → posts it back to OpenMRS
"""
import respx
import httpx

OMRS = "http://openmrs-int-test:9997"
OE   = "http://openelis-int-test:9997"
OMRS_FHIR = f"{OMRS}/openmrs/ws/fhir2/R4"
OE_FHIR   = f"{OE}/fhir/R4"

FHIR_SR = {
    "resourceType": "ServiceRequest",
    "id": "sr-001",
    "status": "active",
    "intent": "order",
    "code": {"coding": [{"code": "CBC", "display": "Complete Blood Count"}]},
    "subject": {"reference": "Patient/omrs-uuid-001"},
    "identifier": [{"value": "SR-001"}],
}

FHIR_SR_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [{"resource": FHIR_SR}],
}

FHIR_DR = {
    "resourceType": "DiagnosticReport",
    "id": "dr-001",
    "status": "final",
    "code": {"text": "CBC"},
    "issued": "2026-03-25T10:00:00Z",
    "subject": {"reference": "Patient/oe-uuid-001"},
}

FHIR_DR_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [{"resource": FHIR_DR}],
}


# ── Hub order routing ─────────────────────────────────────────────────────────

class TestHubLabOrderRouting:
    """Integration Hub routes OpenMRS ServiceRequests to OpenELIS."""

    def test_hub_event_report_final_returns_queued(self, hub_client):
        """The /api/events/report-final endpoint accepts and queues payloads."""
        r = hub_client.post("/api/events/report-final", json={
            "report_id": 10, "order_id": 5,
            "impression": "Haemoglobin 13.2 g/dL — within normal range.",
            "status": "FINAL",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_hub_openelis_service_search_idempotent(self, hub_client):
        """
        The openelis service layer searches before creating to avoid duplicates.
        """
        import asyncio, sys
        existing_patient = {
            "resourceType": "Patient", "id": "oe-existing",
            "identifier": [{"value": "INT001"}],
        }

        with respx.mock:
            respx.get(f"{OE_FHIR}/Patient").mock(
                return_value=httpx.Response(200, json={
                    "entry": [{"resource": existing_patient}]
                })
            )
            respx.put(f"{OE_FHIR}/Patient/oe-existing").mock(
                return_value=httpx.Response(200, json=existing_patient)
            )
            from app.services import openelis
            result = asyncio.get_event_loop().run_until_complete(
                openelis.upsert_patient({
                    "id": "omrs-uuid-001",
                    "identifier": [{"value": "INT001"}],
                    "name": [{"family": "Test", "given": ["Patient"]}],
                })
            )

        # Should return the existing patient id (no create was needed)
        assert result == "oe-existing"

    def test_hub_openmrs_service_posts_diagnostic_report(self, hub_client):
        """OpenMRS service layer correctly POSTs DiagnosticReport."""
        import asyncio

        with respx.mock:
            respx.post(f"{OMRS_FHIR}/DiagnosticReport").mock(
                return_value=httpx.Response(201, json={**FHIR_DR, "id": "created-dr"})
            )
            from app.services import openmrs
            ok = asyncio.get_event_loop().run_until_complete(
                openmrs.post_diagnostic_report(FHIR_DR)
            )

        assert ok is True


# ── Hub result routing ────────────────────────────────────────────────────────

class TestHubLabResultRouting:
    """Integration Hub routes completed OpenELIS results back to OpenMRS."""

    def test_hub_openelis_get_completed_reports(self, hub_client):
        """OpenELIS service layer fetches final DiagnosticReports."""
        import asyncio

        with respx.mock:
            respx.get(f"{OE_FHIR}/DiagnosticReport").mock(
                return_value=httpx.Response(200, json=FHIR_DR_BUNDLE)
            )
            from app.services import openelis
            reports = asyncio.get_event_loop().run_until_complete(
                openelis.get_completed_reports()
            )

        assert len(reports) == 1
        assert reports[0]["id"] == "dr-001"
        assert reports[0]["status"] == "final"

    def test_hub_omrs_get_active_service_requests(self, hub_client):
        """OpenMRS service layer fetches active ServiceRequests."""
        import asyncio

        with respx.mock:
            respx.get(f"{OMRS_FHIR}/ServiceRequest").mock(
                return_value=httpx.Response(200, json=FHIR_SR_BUNDLE)
            )
            from app.services import openmrs
            orders = asyncio.get_event_loop().run_until_complete(
                openmrs.get_active_service_requests()
            )

        assert len(orders) == 1
        assert orders[0]["id"] == "sr-001"
