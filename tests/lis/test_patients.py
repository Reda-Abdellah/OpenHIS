def test_upsert_new_patient(client):
    r = client.post("/api/lab-patients", json={"mrn": "NEW01", "patient_name": "Bob Test"})
    assert r.status_code == 201
    assert r.json()["mrn"] == "NEW01"

def test_upsert_existing_patient(client):
    client.post("/api/lab-patients", json={"mrn": "EX01", "patient_name": "Old Name"})
    r = client.post("/api/lab-patients", json={"mrn": "EX01", "patient_name": "New Name"})
    assert r.status_code == 201
    assert r.json()["patient_name"] == "New Name"

def test_list_patients(client, lab_patient):
    r = client.get("/api/lab-patients")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_search_patient(client, lab_patient):
    r = client.get("/api/lab-patients?q=Jane")
    assert any(p["patient_name"] == "Jane Lab" for p in r.json())
