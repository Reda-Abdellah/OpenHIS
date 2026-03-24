import respx, httpx

ORTHANC_INSTANCES = "http://orthanc:8042/instances"
ORTHANC_SYSTEM   = "http://orthanc:8042/system"

@respx.mock
def test_generate_cr(client):
    respx.post(ORTHANC_INSTANCES).mock(return_value=httpx.Response(
        200, json={"ID": "fake-instance-id"}
    ))
    r = client.post("/api/generate", json={
        "modality": "CR",
        "params": {"body_part": "CHEST", "kVp": 120, "mAs": 5},
        "patient": {"patient_name": "Test^Patient", "patient_id": "TEST001",
                    "patient_birthdate": "19900101", "patient_sex": "M"}
    })
    assert r.status_code == 200
    assert r.json()["modality"] == "CR"
    assert r.json()["count"] >= 1

@respx.mock
def test_generate_ct(client):
    respx.post(ORTHANC_INSTANCES).mock(return_value=httpx.Response(
        200, json={"ID": "ct-instance-id"}
    ))
    r = client.post("/api/generate", json={
        "modality": "CT",
        "params": {"body_part": "ABDOMEN", "slice_thickness": 3.0},
        "patient": {"patient_name": "CT^Patient", "patient_id": "CT001"}
    })
    assert r.status_code == 200
    assert r.json()["modality"] == "CT"

def test_generate_unsupported_modality(client):
    r = client.post("/api/generate", json={
        "modality": "INVALID",
        "params": {},
        "patient": {}
    })
    assert r.status_code == 422

@respx.mock
def test_orthanc_status_reachable(client):
    respx.get(ORTHANC_SYSTEM).mock(return_value=httpx.Response(
        200, json={"Version": "1.12.0", "Name": "Orthanc"}
    ))
    r = client.get("/api/orthanc-status")
    assert r.status_code == 200
    assert r.json()["reachable"] is True

@respx.mock
def test_orthanc_status_unreachable(client):
    respx.get(ORTHANC_SYSTEM).mock(side_effect=httpx.ConnectError("refused"))
    r = client.get("/api/orthanc-status")
    assert r.status_code == 200
    assert r.json()["reachable"] is False
