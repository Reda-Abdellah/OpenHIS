def test_create_encounter(client, patient):
    r = client.post("/api/encounters", json={
        "patient_id": patient["id"], "encounter_type": "inpatient",
        "ward": "ICU", "bed": "3A"
    })
    assert r.status_code == 201
    assert r.json()["ward"] == "ICU"

def test_list_encounters(client, patient):
    client.post("/api/encounters", json={"patient_id": patient["id"], "encounter_type": "outpatient"})
    r = client.get(f"/api/encounters?patient_id={patient['id']}")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_get_encounter(client, patient):
    enc = client.post("/api/encounters", json={
        "patient_id": patient["id"], "encounter_type": "inpatient"
    }).json()
    r = client.get(f"/api/encounters/{enc['id']}")
    assert r.status_code == 200

def test_discharge_encounter(client, patient):
    enc = client.post("/api/encounters", json={
        "patient_id": patient["id"], "encounter_type": "inpatient"
    }).json()
    r = client.patch(f"/api/encounters/{enc['id']}", json={"status": "discharged"})
    assert r.status_code == 200
    assert r.json()["status"] == "discharged"

def test_patient_not_found_on_encounter(client):
    r = client.post("/api/encounters", json={"patient_id": "ghost-id"})
    assert r.status_code == 404
