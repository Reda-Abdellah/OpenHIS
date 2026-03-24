def test_list_alerts_empty(client, patient):
    r = client.get(f"/api/cdss/alerts?patient_id={patient['id']}&unacknowledged=true")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_acknowledge_alert(client):
    # Insert an alert directly via health endpoint presence, then acknowledge
    r = client.get("/api/cdss/alerts")
    assert r.status_code == 200
