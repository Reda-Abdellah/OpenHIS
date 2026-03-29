def test_create_report(client, order):
    r = client.post("/api/reports", json={
        "order_id": order["id"], "radiologist": "Dr. House",
        "technique": "Standard CT", "findings": "No acute findings.",
        "impression": "Normal study.", "status": "DRAFT"
    })
    assert r.status_code == 201
    assert r.json()["status"] == "DRAFT"

def test_duplicate_report_rejected(client, order):
    payload = {"order_id": order["id"], "status": "DRAFT"}
    client.post("/api/reports", json=payload)
    r = client.post("/api/reports", json=payload)
    assert r.status_code == 409

def test_get_report_by_order(client, order):
    client.post("/api/reports", json={"order_id": order["id"]})
    r = client.get(f"/api/reports/order/{order['id']}")
    assert r.status_code == 200
    assert r.json()["order_id"] == order["id"]

def test_update_report_to_final(client, order):
    rep = client.post("/api/reports", json={"order_id": order["id"], "status": "DRAFT"}).json()
    r = client.put(f"/api/reports/{rep['id']}", json={"status": "FINAL", "impression": "Normal."})
    assert r.status_code == 200
    assert r.json()["status"] == "FINAL"

def test_invalid_report_status(client, order):
    r = client.post("/api/reports", json={"order_id": order["id"], "status": "BOGUS"})
    assert r.status_code == 422

def test_list_reports(client, order):
    client.post("/api/reports", json={"order_id": order["id"]})
    r = client.get("/api/reports")
    assert r.status_code == 200
    assert len(r.json()) >= 1
