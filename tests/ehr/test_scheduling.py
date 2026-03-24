def test_create_appointment(client, patient):
    r = client.post("/api/appointments", json={
        "patient_id": patient["id"], "department": "Radiology",
        "scheduled_date": "2026-04-01T09:00:00", "duration_minutes": 30
    })
    assert r.status_code == 201
    assert r.json()["department"] == "Radiology"

def test_list_appointments(client, patient):
    client.post("/api/appointments", json={
        "patient_id": patient["id"], "scheduled_date": "2026-04-02T10:00:00"
    })
    r = client.get(f"/api/appointments?patient_id={patient['id']}")
    assert r.status_code == 200
    assert len(r.json()) >= 1

def test_update_appointment_status(client, patient):
    appt = client.post("/api/appointments", json={
        "patient_id": patient["id"], "scheduled_date": "2026-05-01T08:00:00"
    }).json()
    r = client.patch(f"/api/appointments/{appt['id']}", json={"status": "cancelled"})
    assert r.status_code == 200

def test_appointment_patient_not_found(client):
    r = client.post("/api/appointments", json={"patient_id": "ghost",
                    "scheduled_date": "2026-05-01T08:00:00"})
    assert r.status_code == 404
