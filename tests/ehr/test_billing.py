def test_create_billing_record(client, patient):
    r = client.post("/api/billing", json={
        "patient_id": patient["id"], "cpt_code": "99213",
        "description": "Office visit", "amount": 150.0
    })
    assert r.status_code == 201
    assert r.json()["amount"] == 150.0

def test_list_billing(client, patient):
    client.post("/api/billing", json={"patient_id": patient["id"],
                "cpt_code": "99213", "amount": 200.0})
    r = client.get(f"/api/billing?patient_id={patient['id']}")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_update_billing_status(client, patient):
    rec = client.post("/api/billing", json={
        "patient_id": patient["id"], "cpt_code": "99213", "amount": 100.0
    }).json()
    r = client.patch(f"/api/billing/{rec['id']}/status", json={"status": "paid"})
    assert r.status_code == 200
