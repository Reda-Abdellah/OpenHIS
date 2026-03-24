def test_create_patient(client):
    r = client.post("/api/patients", json={
        "mrn": "P001", "first_name": "John", "last_name": "Doe",
        "birth_date": "1985-03-15", "sex": "M"
    })
    assert r.status_code == 201
    assert r.json()["mrn"] == "P001"

def test_duplicate_mrn(client):
    payload = {"mrn": "DUPX", "first_name": "A", "last_name": "B"}
    client.post("/api/patients", json=payload)
    r = client.post("/api/patients", json=payload)
    assert r.status_code == 409

def test_list_patients(client, patient):
    r = client.get("/api/patients")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_search_patients(client, patient):
    r = client.get("/api/patients?q=Alice")
    assert any(p["first_name"] == "Alice" for p in r.json())

def test_get_patient(client, patient):
    r = client.get(f"/api/patients/{patient['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == patient["id"]

def test_get_patient_not_found(client):
    assert client.get("/api/patients/nonexistent-id").status_code == 404

def test_update_patient(client, patient):
    r = client.patch(f"/api/patients/{patient['id']}", json={"phone": "555-1234"})
    assert r.status_code == 200
    assert r.json()["phone"] == "555-1234"

def test_add_diagnosis(client, patient):
    r = client.post(f"/api/patients/{patient['id']}/diagnoses",
                    json={"icd10_code": "J45.0", "description": "Asthma"})
    assert r.status_code == 201
    assert r.json()["icd10_code"] == "J45.0"

def test_list_diagnoses(client, patient):
    client.post(f"/api/patients/{patient['id']}/diagnoses",
                json={"icd10_code": "E11", "description": "Type 2 Diabetes"})
    r = client.get(f"/api/patients/{patient['id']}/diagnoses")
    assert r.status_code == 200
    assert len(r.json()) >= 1
