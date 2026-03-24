def test_create_specimen(client, lab_patient):
    r = client.post("/api/specimens", json={
        "patient_id": lab_patient["id"], "specimen_type": "urine"
    })
    assert r.status_code == 201
    assert r.json()["specimen_type"] == "urine"
    assert r.json()["accession_number"] is not None

def test_patient_not_found(client):
    r = client.post("/api/specimens", json={"patient_id": 99999, "specimen_type": "blood"})
    assert r.status_code == 404

def test_list_specimens(client, specimen):
    r = client.get("/api/specimens")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_update_specimen_status(client, specimen):
    r = client.patch(f"/api/specimens/{specimen['id']}", json={"status": "processing"})
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
