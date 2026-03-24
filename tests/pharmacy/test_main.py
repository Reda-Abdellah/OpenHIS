"""
Phase 2 — Pharmacy Service Tests
Covers: catalog, prescriptions workflow, dispense, MAR, stock, guard rails.
"""

# ── helpers ───────────────────────────────────────────────────────────────────
def mk_rx(client, patient="P001", drug="Amoxicillin 500mg",
          dose="500mg", freq="BID", qty=10):
    r = client.post("/api/prescriptions", json={
        "ehr_patient_id": patient,
        "drug_name":      drug,
        "dose":           dose,
        "frequency":      freq,
        "quantity":       qty,
        "prescriber":     "Dr. Test",
    })
    assert r.status_code == 201, r.text
    return r.json()

def verify_rx(client, rx_id):
    r = client.post(f"/api/prescriptions/{rx_id}/verify",
                    json={"pharmacist": "Pharmacist A"})
    assert r.status_code == 200, r.text
    return r.json()


# ── Catalog ───────────────────────────────────────────────────────────────────
class TestCatalog:
    def test_seed_medications_loaded(self, client):
        meds = client.get("/api/medications").json()
        assert len(meds) >= 5

    def test_create_medication(self, client):
        r = client.post("/api/medications", json={
            "name": "Vancomycin", "generic_name": "Vancomycin HCl",
            "form": "injection", "strength": "500mg", "route": "iv"
        })
        assert r.status_code == 201
        assert r.json()["name"] == "Vancomycin"

    def test_search_medications(self, client):
        meds = client.get("/api/medications?q=Amox").json()
        assert any("Amox" in m["name"] for m in meds)

    def test_deactivate_medication(self, client):
        meds = client.get("/api/medications").json()
        mid  = meds[0]["id"]
        r    = client.patch(f"/api/medications/{mid}", json={"active": 0})
        assert r.status_code == 200
        assert r.json()["active"] == 0


# ── Prescription workflow ─────────────────────────────────────────────────────
class TestPrescriptionWorkflow:
    def test_create_prescription(self, client):
        rx = mk_rx(client)
        assert rx["status"]   == "pending"
        assert rx["drug_name"]== "Amoxicillin 500mg"

    def test_verify_prescription(self, client):
        rx   = mk_rx(client)
        vx   = verify_rx(client, rx["id"])
        assert vx["status"]      == "verified"
        assert vx["verified_by"] == "Pharmacist A"

    def test_cannot_verify_twice(self, client):
        rx = mk_rx(client)
        verify_rx(client, rx["id"])
        r  = client.post(f"/api/prescriptions/{rx['id']}/verify",
                         json={"pharmacist": "X"})
        assert r.status_code == 409

    def test_cancel_pending(self, client):
        rx = mk_rx(client)
        r  = client.post(f"/api/prescriptions/{rx['id']}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_cannot_cancel_dispensed(self, client):
        rx = mk_rx(client)
        verify_rx(client, rx["id"])
        client.post("/api/dispenses", json={
            "prescription_id": rx["id"], "quantity": 1, "dispensed_by": "P"
        })
        r = client.post(f"/api/prescriptions/{rx['id']}/cancel")
        assert r.status_code == 409

    def test_filter_by_status(self, client):
        mk_rx(client, patient="P001")
        mk_rx(client, patient="P002")
        pending = client.get("/api/prescriptions?status=pending").json()
        assert all(p["status"] == "pending" for p in pending)


# ── Dispensing ────────────────────────────────────────────────────────────────
class TestDispensing:
    def test_dispense_verified_rx(self, client):
        rx = mk_rx(client)
        verify_rx(client, rx["id"])
        r  = client.post("/api/dispenses", json={
            "prescription_id": rx["id"], "quantity": 5, "dispensed_by": "Tech A"
        })
        assert r.status_code == 201
        # Prescription should now be dispensed
        updated = client.get(f"/api/prescriptions/{rx['id']}").json()
        assert updated["status"] == "dispensed"

    def test_cannot_dispense_pending_rx(self, client):
        rx = mk_rx(client)
        r  = client.post("/api/dispenses", json={
            "prescription_id": rx["id"], "quantity": 1
        })
        assert r.status_code == 409

    def test_dispense_decrements_stock(self, client):
        meds    = client.get("/api/medications").json()
        med_id  = meds[0]["id"]
        stock_before = client.get("/api/stock").json()
        qty_before   = next((s["quantity"] for s in stock_before if s["medication_id"] == med_id), 100)

        rx = client.post("/api/prescriptions", json={
            "ehr_patient_id": "P001", "drug_name": meds[0]["name"],
            "medication_id": med_id, "dose": "1 tab",
            "frequency": "QD", "quantity": 5
        }).json()
        verify_rx(client, rx["id"])
        client.post("/api/dispenses", json={"prescription_id": rx["id"], "quantity": 5})

        stock_after = client.get("/api/stock").json()
        qty_after   = next((s["quantity"] for s in stock_after if s["medication_id"] == med_id), 0)
        assert qty_after == qty_before - 5


# ── MAR ───────────────────────────────────────────────────────────────────────
class TestMAR:
    def test_record_administration(self, client):
        rx = mk_rx(client)
        verify_rx(client, rx["id"])
        client.post("/api/dispenses", json={"prescription_id": rx["id"], "quantity": 1})
        r  = client.post("/api/mar", json={
            "prescription_id": rx["id"], "administered_by": "Nurse B",
            "status": "given"
        })
        assert r.status_code == 201
        assert r.json()["status"] == "given"

    def test_mar_held_status(self, client):
        rx = mk_rx(client)
        r  = client.post("/api/mar", json={
            "prescription_id": rx["id"], "status": "held",
            "notes": "Patient NPO"
        })
        assert r.status_code == 201
        assert r.json()["status"] == "held"


# ── Stock ─────────────────────────────────────────────────────────────────────
class TestStock:
    def test_restock(self, client):
        stock  = client.get("/api/stock").json()
        sid    = stock[0]["id"]
        qty    = stock[0]["quantity"]
        r      = client.patch(f"/api/stock/{sid}", json={"quantity_delta": 50})
        assert r.status_code == 200
        assert r.json()["quantity"] == qty + 50

    def test_negative_stock_rejected(self, client):
        stock = client.get("/api/stock").json()
        sid   = stock[0]["id"]
        r     = client.patch(f"/api/stock/{sid}", json={"quantity_delta": -999999})
        assert r.status_code == 409

    def test_stock_alerts(self, client):
        stock  = client.get("/api/stock").json()
        sid    = stock[0]["id"]
        qty    = stock[0]["quantity"]
        client.patch(f"/api/stock/{sid}", json={"quantity_delta": -(qty - 5)})
        alerts = client.get("/api/stock/alerts").json()
        assert any(a["id"] == sid for a in alerts)
