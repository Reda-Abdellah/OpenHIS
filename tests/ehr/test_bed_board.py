"""
Phase 1 — Bed Management Board
Tests cover:
  - Bed CRUD (create, read, filter, update, delete)
  - Duplicate prevention
  - Occupancy sync with encounters (admit → occupied, discharge → housekeeping)
  - Conflict prevention (two patients cannot share a bed)
  - Board grouping and occupancy counts
  - Stats endpoint
"""
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def mk_patient(client, mrn="P001"):
    r = client.post("/api/patients", json={
        "mrn": mrn, "first_name": "Jane", "last_name": "Doe",
        "birth_date": "1985-06-15", "sex": "female"
    })
    assert r.status_code == 201, r.text
    return r.json()


def mk_bed(client, ward="Cardiology", label="101-A", btype="standard"):
    r = client.post("/api/beds", json={
        "ward": ward, "room": "101", "bed_label": label, "bed_type": btype
    })
    assert r.status_code == 201, r.text
    return r.json()


def admit(client, patient_id, ward="Cardiology", bed="101-A"):
    r = client.post("/api/encounters", json={
        "patient_id": patient_id,
        "encounter_type": "inpatient",
        "ward": ward,
        "bed": bed,
        "attending_physician": "Dr. House"
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── Bed CRUD ──────────────────────────────────────────────────────────────────

class TestBedCRUD:
    def test_create_bed(self, client):
        bed = mk_bed(client)
        assert bed["ward"]      == "Cardiology"
        assert bed["bed_label"] == "101-A"
        assert bed["status"]    == "available"

    def test_get_bed(self, client):
        bed = mk_bed(client)
        r = client.get(f"/api/beds/{bed['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == bed["id"]

    def test_list_beds(self, client):
        mk_bed(client, label="101-A")
        mk_bed(client, label="101-B")
        beds = client.get("/api/beds").json()
        assert len(beds) == 2

    def test_delete_bed(self, client):
        bed = mk_bed(client)
        r = client.delete(f"/api/beds/{bed['id']}")
        assert r.status_code == 204
        assert client.get(f"/api/beds/{bed['id']}").status_code == 404

    def test_update_bed_status(self, client):
        bed = mk_bed(client)
        r = client.patch(f"/api/beds/{bed['id']}", json={"status": "maintenance"})
        assert r.status_code == 200
        assert r.json()["status"] == "maintenance"

    def test_filter_by_ward(self, client):
        mk_bed(client, ward="Cardiology", label="101-A")
        mk_bed(client, ward="Neurology",  label="201-A")
        beds = client.get("/api/beds?ward=Cardiology").json()
        assert len(beds) == 1
        assert beds[0]["ward"] == "Cardiology"

    def test_filter_by_status(self, client):
        b1 = mk_bed(client, label="101-A")
        mk_bed(client, label="101-B")
        client.patch(f"/api/beds/{b1['id']}", json={"status": "maintenance"})
        available = client.get("/api/beds?status=available").json()
        assert len(available) == 1

    def test_duplicate_bed_rejected(self, client):
        mk_bed(client, label="101-A")
        r = client.post("/api/beds", json={
            "ward": "Cardiology", "room": "101",
            "bed_label": "101-A", "bed_type": "standard"
        })
        assert r.status_code == 409


# ── Occupancy sync ────────────────────────────────────────────────────────────

class TestOccupancySync:
    def test_admit_marks_bed_occupied(self, client):
        p = mk_patient(client)
        mk_bed(client, ward="Cardiology", label="101-A")
        admit(client, p["id"], ward="Cardiology", bed="101-A")
        beds = client.get("/api/beds?ward=Cardiology").json()
        assert beds[0]["status"]      == "occupied"
        assert "Doe" in (beds[0].get("patient_name") or "")

    def test_discharge_marks_bed_housekeeping(self, client):
        p   = mk_patient(client)
        mk_bed(client, ward="Cardiology", label="101-A")
        enc = admit(client, p["id"], ward="Cardiology", bed="101-A")
        r   = client.patch(f"/api/encounters/{enc['id']}",
                           json={"status": "discharged"})
        assert r.status_code == 200
        beds = client.get("/api/beds?ward=Cardiology").json()
        assert beds[0]["status"] == "housekeeping"

    def test_admit_to_occupied_bed_rejected(self, client):
        p1 = mk_patient(client, mrn="P001")
        p2 = mk_patient(client, mrn="P002")
        mk_bed(client, ward="Cardiology", label="101-A")
        admit(client, p1["id"], ward="Cardiology", bed="101-A")
        r = client.post("/api/encounters", json={
            "patient_id": p2["id"],
            "encounter_type": "inpatient",
            "ward": "Cardiology",
            "bed": "101-A"
        })
        assert r.status_code == 409

    def test_admit_to_housekeeping_bed_rejected(self, client):
        p = mk_patient(client)
        bed = mk_bed(client, label="101-A")
        client.patch(f"/api/beds/{bed['id']}", json={"status": "housekeeping"})
        r = client.post("/api/encounters", json={
            "patient_id": p["id"], "encounter_type": "inpatient",
            "ward": "Cardiology", "bed": "101-A"
        })
        assert r.status_code == 409

    def test_admit_without_bed_still_works(self, client):
        """Encounters without ward/bed should be unaffected."""
        p = mk_patient(client)
        r = client.post("/api/encounters", json={
            "patient_id": p["id"], "encounter_type": "outpatient"
        })
        assert r.status_code == 201


# ── Board view ────────────────────────────────────────────────────────────────

class TestBoardView:
    def test_board_groups_by_ward(self, client):
        mk_bed(client, ward="Cardiology", label="101-A")
        mk_bed(client, ward="Cardiology", label="101-B")
        mk_bed(client, ward="Neurology",  label="201-A")
        board = {w["ward"]: w for w in client.get("/api/beds/board").json()}
        assert "Cardiology" in board
        assert "Neurology"  in board
        assert board["Cardiology"]["total"] == 2
        assert board["Neurology"]["total"]  == 1

    def test_board_counts_occupancy(self, client):
        p = mk_patient(client)
        mk_bed(client, ward="Cardiology", label="101-A")
        mk_bed(client, ward="Cardiology", label="101-B")
        admit(client, p["id"], ward="Cardiology", bed="101-A")
        ward = next(w for w in client.get("/api/beds/board").json()
                    if w["ward"] == "Cardiology")
        assert ward["occupied"]  == 1
        assert ward["available"] == 1

    def test_stats_endpoint(self, client):
        mk_bed(client, label="101-A")
        mk_bed(client, label="101-B")
        stats = {s["status"]: s["count"] for s in client.get("/api/beds/stats").json()}
        assert stats.get("available", 0) == 2
