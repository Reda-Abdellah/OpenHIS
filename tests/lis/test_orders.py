def test_create_lab_order(client, specimen):
    r = client.post("/api/lab-orders", json={"specimen_id": specimen["id"], "test_code": "BMP"})
    assert r.status_code == 201
    assert r.json()["test_code"] == "BMP"

def test_catalog_endpoint(client):
    r = client.get("/api/lab-orders/catalog")
    assert r.status_code == 200
    assert "CBC" in [c["code"] for c in r.json()]

def test_specimen_not_found(client):
    r = client.post("/api/lab-orders", json={"specimen_id": 99999, "test_code": "CBC"})
    assert r.status_code == 404

def test_list_lab_orders(client, lab_order):
    r = client.get("/api/lab-orders")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_update_order(client, lab_order):
    r = client.patch(f"/api/lab-orders/{lab_order['id']}", json={"status": "INPROGRESS"})
    assert r.status_code == 200
