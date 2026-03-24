def test_create_patient(client):
    r = client.post("/api/patients", json={"mrn": "MRN123", "patient_name": "Jane Doe"})
    assert r.status_code == 201
    assert r.json()["patient_name"] == "Jane Doe"

def test_duplicate_patient(client):
    client.post("/api/patients", json={"mrn": "DUP01", "patient_name": "Dup"})
    r = client.post("/api/patients", json={"mrn": "DUP01", "patient_name": "Dup"})
    assert r.status_code == 409

def test_list_patients(client, patient):
    r = client.get("/api/patients")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_search_patients(client, patient):
    r = client.get("/api/patients?q=Test")
    assert any(p["patient_name"] == "Test Patient" for p in r.json())

def test_update_patient(client, patient):
    r = client.patch(f"/api/patients/{patient['id']}",
                     json={"patient_name": "Updated Name"})
    assert r.status_code == 200
    assert r.json()["patient_name"] == "Updated Name"
