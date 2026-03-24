def test_create_order(client, patient):
    r = client.post("/api/orders", json={
        "patient_id": patient["id"], "order_type": "LAB",
        "order_detail": {"test": "CBC"}, "requesting_physician": "Dr. House"
    })
    assert r.status_code == 201
    assert r.json()["order_type"] == "LAB"

def test_list_orders(client, patient):
    client.post("/api/orders", json={"patient_id": patient["id"], "order_type": "IMAGING"})
    r = client.get(f"/api/orders?patient_id={patient['id']}")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_cancel_order(client, patient):
    order = client.post("/api/orders", json={
        "patient_id": patient["id"], "order_type": "LAB"
    }).json()
    r = client.patch(f"/api/orders/{order['id']}", json={"status": "CANCELLED"})
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"

def test_order_patient_not_found(client):
    r = client.post("/api/orders", json={"patient_id": "ghost", "order_type": "LAB"})
    assert r.status_code == 404
