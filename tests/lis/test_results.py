def test_submit_results(client, lab_order):
    r = client.post("/api/results", json={
        "order_id": lab_order["id"],
        "results": [{"analyte": "WBC", "value": "6.5", "unit": "10^9/L", "flag": "normal"}],
        "status": "preliminary"
    })
    assert r.status_code in (200, 201)

def test_get_results_for_order(client, lab_order):
    client.post("/api/results", json={
        "order_id": lab_order["id"],
        "results": [{"analyte": "Hemoglobin", "value": "13.0", "unit": "g/dL"}]
    })
    r = client.get(f"/api/results/order/{lab_order['id']}")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_validate_result(client, lab_order):
    client.post("/api/results", json={
        "order_id": lab_order["id"],
        "results": [{"analyte": "Na", "value": "140", "unit": "mmol/L"}]
    })
    results = client.get(f"/api/results/order/{lab_order['id']}").json()
    assert len(results) >= 1
    rid = results[0]["id"]
    r = client.patch(f"/api/results/{rid}/validate", json={"validated_by": "labtech1"})
    assert r.status_code == 200
    assert r.json()["status"] == "final"
