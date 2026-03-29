def test_create_order(client, patient):
    r = client.post("/api/orders", json={
        "patient_id": patient["id"], "modality": "MR", "body_part": "BRAIN"
    })
    assert r.status_code == 201
    assert r.json()["modality"] == "MR"
    assert r.json()["accession_number"] is not None

def test_list_orders(client, order):
    r = client.get("/api/orders")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_list_orders_by_status(client, order):
    r = client.get("/api/orders?status=PENDING")
    assert all(o["status"] == "PENDING" for o in r.json())

def test_update_order_status(client, order):
    r = client.patch(f"/api/orders/{order['id']}", json={"status": "INPROGRESS"})
    assert r.status_code == 200
    assert r.json()["status"] == "INPROGRESS"

def test_cancel_order(client, order):
    r = client.delete(f"/api/orders/{order['id']}")
    assert r.status_code == 204

def test_cancel_nonexistent_order(client):
    assert client.delete("/api/orders/99999").status_code == 404
