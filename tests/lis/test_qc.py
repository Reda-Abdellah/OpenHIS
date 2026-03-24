def test_record_passing_qc(client):
    r = client.post("/api/qc", json={
        "instrument_id": "HEMA-01", "test_code": "CBC", "lot_number": "L001",
        "qc_level": "normal", "result_value": 7.5,
        "expected_mean": 7.0, "expected_sd": 0.5
    })
    assert r.status_code == 201
    assert r.json()["westgard_flag"] in ("pass", "1-2s")

def test_record_failing_qc_13s(client):
    r = client.post("/api/qc", json={
        "instrument_id": "HEMA-01", "test_code": "CBC", "lot_number": "L001",
        "qc_level": "high", "result_value": 12.0,
        "expected_mean": 7.0, "expected_sd": 1.5
    })
    assert r.status_code == 201
    assert r.json()["westgard_flag"] == "1-3s"
    assert r.json()["passed"] == 0

def test_list_qc_records(client):
    client.post("/api/qc", json={
        "instrument_id": "CHEM-01", "test_code": "BMP", "lot_number": "L002",
        "qc_level": "low", "result_value": 5.0,
        "expected_mean": 5.0, "expected_sd": 0.3
    })
    r = client.get("/api/qc?instrument_id=CHEM-01")
    assert r.status_code == 200
    assert len(r.json()) >= 1
