"""
DICOM validation tests.

Tests that generated DICOM files:
- Are parseable by pydicom
- Contain all required Type 1 attributes
- Have correct modality-specific tags
- Have valid pixel data with expected dimensions
- Have consistent UIDs (StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID)
"""
import io, sys, os, pytest
from pathlib import Path
import pydicom

SERVICE = str(Path(__file__).parent.parent.parent.parent / "services" / "simulator")
if SERVICE not in sys.path:
    sys.path.insert(0, SERVICE)


# ─── helpers ──────────────────────────────────────────────────────────────────

PATIENT = {
    "patient_name": "TEST^PATIENT",
    "patient_id":   "SIM-TEST-001",
    "dob":          "1980-06-15",
    "sex":          "M",
    "accession":    "ACC-TEST-001",
}


def _load(raw: bytes) -> pydicom.Dataset:
    """Parse raw DICOM bytes, raising a helpful message if it fails."""
    try:
        return pydicom.dcmread(io.BytesIO(raw))
    except Exception as exc:
        pytest.fail(f"pydicom could not parse generated DICOM: {exc}")


def _check_required_tags(ds: pydicom.Dataset, modality: str):
    """Assert Type 1 / 2 tags that every DICOM instance must have."""
    # Patient module
    assert str(ds.PatientName), "PatientName must not be empty"
    assert ds.PatientID,        "PatientID must not be empty"

    # Study module
    assert ds.StudyInstanceUID, "StudyInstanceUID required"
    assert ds.StudyDate,        "StudyDate required"

    # Series module
    assert ds.SeriesInstanceUID, "SeriesInstanceUID required"
    assert ds.Modality == modality, f"Expected Modality={modality}, got {ds.Modality}"

    # SOP common
    assert ds.SOPClassUID,    "SOPClassUID required"
    assert ds.SOPInstanceUID, "SOPInstanceUID required"

    # Image pixel module
    assert ds.Rows    > 0, "Rows must be positive"
    assert ds.Columns > 0, "Columns must be positive"
    assert hasattr(ds, "PixelData"), "PixelData required"


# ─── CR tests ─────────────────────────────────────────────────────────────────

class TestCRDICOM:

    @pytest.fixture
    def cr_instances(self):
        from dicom_factory import build_dicom
        return build_dicom("CR", {"body_part": "CHEST", "rows": 512, "cols": 512}, PATIENT)

    def test_cr_produces_one_instance(self, cr_instances):
        assert len(cr_instances) == 1, "CR should produce exactly one DICOM instance"

    def test_cr_is_parseable(self, cr_instances):
        _load(cr_instances[0])   # raises on failure

    def test_cr_required_tags(self, cr_instances):
        ds = _load(cr_instances[0])
        _check_required_tags(ds, "CR")

    def test_cr_patient_data_matches(self, cr_instances):
        ds = _load(cr_instances[0])
        assert ds.PatientID == "SIM-TEST-001"
        assert "TEST" in str(ds.PatientName) or "PATIENT" in str(ds.PatientName)

    def test_cr_bits_stored(self, cr_instances):
        ds = _load(cr_instances[0])
        assert ds.BitsAllocated == 16
        assert ds.BitsStored == 12

    def test_cr_pixel_data_correct_size(self, cr_instances):
        ds = _load(cr_instances[0])
        expected_bytes = ds.Rows * ds.Columns * (ds.BitsAllocated // 8)
        actual_bytes   = len(ds.PixelData)
        assert actual_bytes == expected_bytes, (
            f"Pixel data size mismatch: expected {expected_bytes}, got {actual_bytes}"
        )

    def test_cr_body_part_tag(self, cr_instances):
        ds = _load(cr_instances[0])
        assert ds.BodyPartExamined == "CHEST"

    def test_cr_window_center_width(self, cr_instances):
        ds = _load(cr_instances[0])
        assert ds.WindowCenter is not None
        assert ds.WindowWidth  is not None

    def test_cr_different_body_parts(self):
        from dicom_factory import build_dicom
        for body in ("HAND", "KNEE", "SPINE", "SKULL"):
            instances = build_dicom("CR", {"body_part": body, "rows": 256, "cols": 256},
                                    PATIENT)
            ds = _load(instances[0])
            assert ds.BodyPartExamined == body


# ─── CT tests ─────────────────────────────────────────────────────────────────

class TestCTDICOM:

    @pytest.fixture
    def ct_instances(self):
        from dicom_factory import build_dicom
        return build_dicom("CT", {
            "body_part": "CHEST", "rows": 64, "cols": 64,
            "slice_count": 4, "slice_thickness": 5.0,
        }, PATIENT)

    def test_ct_produces_multiple_instances(self, ct_instances):
        assert len(ct_instances) == 4, "CT should produce one instance per slice"

    def test_ct_all_instances_parseable(self, ct_instances):
        for raw in ct_instances:
            _load(raw)

    def test_ct_required_tags_all_slices(self, ct_instances):
        for raw in ct_instances:
            ds = _load(raw)
            _check_required_tags(ds, "CT")

    def test_ct_shared_study_uid(self, ct_instances):
        """All CT slices must share the same StudyInstanceUID."""
        uids = [_load(r).StudyInstanceUID for r in ct_instances]
        assert len(set(uids)) == 1, "All slices must share StudyInstanceUID"

    def test_ct_shared_series_uid(self, ct_instances):
        """All CT slices must share the same SeriesInstanceUID."""
        uids = [_load(r).SeriesInstanceUID for r in ct_instances]
        assert len(set(uids)) == 1, "All slices must share SeriesInstanceUID"

    def test_ct_unique_sop_instance_uids(self, ct_instances):
        """Each CT slice must have a unique SOPInstanceUID."""
        uids = [_load(r).SOPInstanceUID for r in ct_instances]
        assert len(uids) == len(set(uids)), "SOPInstanceUIDs must be unique per slice"

    def test_ct_instance_numbers_sequential(self, ct_instances):
        numbers = sorted(_load(r).InstanceNumber for r in ct_instances)
        assert numbers == list(range(1, len(ct_instances) + 1))

    def test_ct_slice_locations_increasing(self, ct_instances):
        locations = [_load(r).SliceLocation for r in ct_instances]
        assert locations == sorted(locations), "Slice locations should be increasing"

    def test_ct_hu_range(self, ct_instances):
        """CT pixel values should be in a reasonable HU range when rescaled."""
        import numpy as np
        ds = _load(ct_instances[0])
        pixels  = np.frombuffer(ds.PixelData, dtype=np.int16)
        rescaled = pixels.astype(np.float32) * ds.RescaleSlope + ds.RescaleIntercept
        assert rescaled.min() >= -1100, "HU values below -1100 are unphysical"
        assert rescaled.max() <= 3100,  "HU values above 3100 are unphysical"

    def test_ct_pixel_representation_signed(self, ct_instances):
        ds = _load(ct_instances[0])
        assert ds.PixelRepresentation == 1, "CT pixels must be signed (HU can be negative)"


# ─── MR tests ─────────────────────────────────────────────────────────────────

class TestMRDICOM:

    @pytest.fixture
    def mr_instances(self):
        from dicom_factory import build_dicom
        return build_dicom("MR", {
            "body_part": "BRAIN", "rows": 64, "cols": 64,
            "slice_count": 3, "sequence_type": "T1",
        }, PATIENT)

    def test_mr_produces_multiple_slices(self, mr_instances):
        assert len(mr_instances) == 3

    def test_mr_modality_tag(self, mr_instances):
        for raw in mr_instances:
            ds = _load(raw)
            assert ds.Modality == "MR"

    def test_mr_unique_sop_uids(self, mr_instances):
        uids = [_load(r).SOPInstanceUID for r in mr_instances]
        assert len(set(uids)) == len(uids)

    def test_mr_shared_study_uid(self, mr_instances):
        uids = [_load(r).StudyInstanceUID for r in mr_instances]
        assert len(set(uids)) == 1

    def test_mr_sequence_name_tag(self, mr_instances):
        ds = _load(mr_instances[0])
        assert hasattr(ds, "SequenceName"), "MR should have SequenceName tag"


# ─── US tests ─────────────────────────────────────────────────────────────────

class TestUSDICOM:

    @pytest.fixture
    def us_instances(self):
        from dicom_factory import build_dicom
        return build_dicom("US", {
            "body_part": "ABDOMEN", "rows": 120, "cols": 160,
            "probe_type": "Convex", "depth": 15, "frequency": 3.5,
        }, PATIENT)

    def test_us_produces_one_instance(self, us_instances):
        assert len(us_instances) == 1

    def test_us_required_tags(self, us_instances):
        ds = _load(us_instances[0])
        _check_required_tags(ds, "US")

    def test_us_8bit_pixel(self, us_instances):
        ds = _load(us_instances[0])
        assert ds.BitsAllocated == 8
        assert ds.BitsStored == 8


# ─── DX tests ─────────────────────────────────────────────────────────────────

class TestDXDICOM:

    def test_dx_produces_one_instance(self):
        from dicom_factory import build_dicom
        instances = build_dicom("DX", {
            "body_part": "CHEST", "rows": 256, "cols": 256
        }, PATIENT)
        assert len(instances) == 1
        ds = _load(instances[0])
        assert ds.Modality == "DX"

    def test_dx_required_tags(self):
        from dicom_factory import build_dicom
        instances = build_dicom("DX", {"body_part": "HAND", "rows": 128, "cols": 128}, PATIENT)
        ds = _load(instances[0])
        _check_required_tags(ds, "DX")


# ─── unsupported modality ─────────────────────────────────────────────────────

class TestUnsupportedModality:

    def test_unsupported_modality_raises(self):
        from dicom_factory import build_dicom
        with pytest.raises((ValueError, KeyError)):
            build_dicom("XR", {}, PATIENT)

    def test_supported_list(self):
        from dicom_factory import SUPPORTED
        assert set(SUPPORTED) >= {"CR", "DX", "CT", "MR", "US"}


# ─── API endpoint: generate + orthanc upload ─────────────────────────────────

class TestSimulatorGenerateEndpoint:
    """Test the /api/generate endpoint's DICOM construction + Orthanc upload."""

    @pytest.fixture
    def sim_client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORTHANC_URL", "http://orthanc:8042")
        for mod in [k for k in sys.modules
                    if k in ('main', 'database', 'dicom_factory', 'presets')
                    or k.startswith('routers.')]:
            del sys.modules[mod]
        if SERVICE in sys.path:
            sys.path.remove(SERVICE)
        sys.path.insert(0, SERVICE)
        from main import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_generate_cr_stores_in_orthanc(self, sim_client):
        import respx, httpx
        with respx.mock:
            respx.post("http://orthanc:8042/instances").mock(
                return_value=httpx.Response(200, json={"ID": "test-instance-uid-001"})
            )
            r = sim_client.post("/api/generate", json={
                "modality": "CR",
                "params": {"body_part": "CHEST", "rows": 256, "cols": 256},
                "patient": {
                    "patient_name": "DICOM^TEST",
                    "patient_id": "DCM001",
                }
            })
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["modality"] == "CR"
        assert job["count"] == 1
        assert "test-instance-uid-001" in job["instance_ids"]

    def test_generate_ct_sends_multiple_instances_to_orthanc(self, sim_client):
        import respx, httpx
        call_count = {"n": 0}

        def side_effect(req):
            call_count["n"] += 1
            return httpx.Response(200, json={"ID": f"ct-uid-{call_count['n']}"})

        with respx.mock:
            respx.post("http://orthanc:8042/instances").mock(side_effect=side_effect)
            r = sim_client.post("/api/generate", json={
                "modality": "CT",
                "params": {"body_part": "CHEST", "rows": 64, "cols": 64, "slice_count": 4},
                "patient": {"patient_name": "CT^TEST", "patient_id": "DCM002"}
            })
        assert r.status_code == 200
        job = r.json()
        assert job["count"] == 4
        assert call_count["n"] == 4, "Orthanc must receive one POST per CT slice"

    def test_generate_orthanc_rejection_returns_502(self, sim_client):
        import respx, httpx
        with respx.mock:
            respx.post("http://orthanc:8042/instances").mock(
                return_value=httpx.Response(400, text="Bad DICOM")
            )
            r = sim_client.post("/api/generate", json={
                "modality": "CR",
                "params": {"body_part": "HAND", "rows": 128, "cols": 128},
                "patient": {"patient_name": "REJECT^TEST", "patient_id": "DCM003"}
            })
        assert r.status_code == 502

    def test_generate_unsupported_modality_returns_422(self, sim_client):
        import respx
        with respx.mock:
            r = sim_client.post("/api/generate", json={
                "modality": "PET",
                "params": {},
                "patient": {}
            })
        assert r.status_code == 422

    def test_generate_job_recorded_in_history(self, sim_client):
        import respx, httpx
        with respx.mock:
            respx.post("http://orthanc:8042/instances").mock(
                return_value=httpx.Response(200, json={"ID": "hist-001"})
            )
            sim_client.post("/api/generate", json={
                "modality": "CR",
                "params": {"body_part": "KNEE", "rows": 128, "cols": 128},
                "patient": {"patient_name": "HIST^TEST", "patient_id": "DCM004"}
            })

        r = sim_client.get("/api/jobs")
        assert r.status_code == 200
        jobs = r.json()
        assert len(jobs) >= 1
        assert jobs[0]["modality"] == "CR"
