"""
Scenario 3 — DICOM Imaging Workflow

Mirrors SCENARIO 3 in docs/verification_and_validation/v-and-v-scenario.md.

Full imaging pipeline validated end-to-end:

    simulator /api/generate
      → Orthanc (DICOM store)
      → Orthanc webhook → integration-hub (ImagingStudy push to OpenMRS)
      → ai-controller auto-trigger (poc-xray pipeline for CR modality)
      → AI job runs in container, produces Observation
      → ai-controller → hub → OpenMRS (Observation push)

Covers:
  ✅ S3.1 — Orthanc /system reachable (DICOM store is up)
  ✅ S3.2 — ai-controller /api/pipelines lists at least poc-xray
  ✅ S3.3 — simulator /api/generate accepts a CR request, returns instance id
  ✅ S3.4 — Orthanc stores the instance (counts increment)
  ✅ S3.5 — integration-hub audit captures orthanc→hub + hub→omrs flow
  ✅ S3.6 — ai-controller /api/jobs shows a job for the new study
  ✅ S3.7 — AI job completes (status COMPLETED, non-empty result_summary)
"""
import time

import pytest


pytestmark = pytest.mark.e2e


class TestS3_DICOMImaging:

    def test_s3_1_orthanc_reachable(self, orthanc):
        r = orthanc.get("/system")
        assert r.status_code == 200
        body = r.json()
        assert "ApiVersion" in body and "Version" in body

    def test_s3_2_pipelines_registered(self, ai_api):
        r = ai_api.get("/pipelines")
        assert r.status_code == 200
        pipelines = r.json()
        ids = {p["id"] for p in pipelines}
        assert "poc-xray" in ids, f"poc-xray missing from pipelines: {ids}"

    def test_s3_3_simulator_generates_cr_study(self, simulator_api, orthanc, request):
        """Ask the simulator to produce one CR chest-PA instance."""
        before = orthanc.get("/statistics").json()["CountInstances"]

        r = simulator_api.post("/generate", json={
            "modality": "CR",
            "patient":  {
                "id":        "E2E-IMG-001",
                "name":      "E2E^Imaging",
                "birthdate": "19800101",
                "sex":       "M",
            },
            "params": {
                "body_part":     "CHEST",
                "view_position": "PA",
            },
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["modality"]       == "CR"
        assert body["body_part"]      == "CHEST"
        assert isinstance(body["instance_ids"], list) and body["instance_ids"]
        request.config.cache.set("s3/instance_ids",    body["instance_ids"])
        request.config.cache.set("s3/before_instances", before)

    def test_s3_4_orthanc_stored_instance(self, orthanc, request):
        before = request.config.cache.get("s3/before_instances", 0)
        # Allow a few hundred ms for the simulator's POST → Orthanc sequence.
        for _ in range(10):
            after = orthanc.get("/statistics").json()["CountInstances"]
            if after > before:
                break
            time.sleep(0.3)
        else:
            pytest.fail(f"Orthanc instance count did not increase from {before}")
        assert after >= before + 1

        # Instance is fetchable
        inst_id = request.config.cache.get("s3/instance_ids", [None])[0]
        assert inst_id
        r = orthanc.get(f"/instances/{inst_id}")
        assert r.status_code == 200

    def test_s3_5_hub_audit_captures_imaging_flow(self, hub_api):
        """
        Within ~5s the integration-hub should log:
            orthanc→hub  ImagingStudy webhook_received
            hub→omrs     ImagingStudy fhir_pushed ok
        """
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            r = hub_api.get("/audit", params={"limit": 50})
            assert r.status_code == 200
            events = r.json().get("events", [])
            saw_webhook = any(
                e["event_type"] == "webhook_received"
                and e["direction"] == "orthanc→hub"
                and e["resource_type"] == "ImagingStudy"
                for e in events
            )
            saw_push = any(
                e["event_type"] == "fhir_pushed"
                and e["direction"] == "hub→omrs"
                and e["resource_type"] == "ImagingStudy"
                and e["status"] == "ok"
                for e in events
            )
            if saw_webhook and saw_push:
                return
            time.sleep(0.5)
        pytest.fail(
            "hub audit did not record orthanc→hub + hub→omrs ImagingStudy flow "
            "within 8s — Orthanc webhook or FHIR push path is broken"
        )

    def test_s3_6_ai_controller_created_job(self, ai_api, request):
        """A new AI job appears for the study within ~10s of the Orthanc push."""
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            r = ai_api.get("/jobs", params={"limit": 20})
            assert r.status_code == 200
            jobs = r.json()
            cr_jobs = [j for j in jobs if j["modality"] == "CR" and j["pipeline_id"] == "poc-xray"]
            if cr_jobs:
                request.config.cache.set("s3/job_id", cr_jobs[0]["id"])
                return
            time.sleep(0.5)
        pytest.fail("ai-controller did not auto-create a poc-xray job within 12s")

    def test_s3_7_ai_job_completes(self, ai_api, request):
        """The poc-xray job reaches COMPLETED with a non-empty result_summary."""
        job_id = request.config.cache.get("s3/job_id", None)
        assert job_id

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            r = ai_api.get(f"/jobs/{job_id}")
            assert r.status_code == 200
            job = r.json()
            if job["status"] == "COMPLETED":
                assert job["result_summary"], "result_summary empty for completed job"
                return
            if job["status"] in ("FAILED", "ERROR"):
                pytest.fail(f"poc-xray job ended in {job['status']}: {job.get('error')}")
            time.sleep(0.5)
        pytest.fail(f"AI job {job_id} did not complete within 15s")
