"""
Phase 4 — MPI Tests
Covers: master patients CRUD, cross-references, sync from EHR,
        lookup, matching algorithm, merge, guard-rails, audit log.
"""

# ── helpers ───────────────────────────────────────────────────────────────────
def mk_patient(client, mrn="MRN-001", first="Jane", last="Doe",
               dob="1985-03-15", sex="female"):
    r = client.post("/api/patients", json={
        "mrn": mrn, "firstname": first, "lastname": last,
        "birthdate": dob, "sex": sex
    })
    assert r.status_code == 201, r.text
    return r.json()


def sync_ehr(client, ehr_id="P-1", mrn="MRN-001",
             first="Jane", last="Doe", dob="1985-03-15"):
    r = client.post("/api/sync/from-ehr", json={
        "id": ehr_id, "mrn": mrn, "firstname": first,
        "lastname": last, "birthdate": dob, "sex": "female"
    })
    assert r.status_code == 200, r.text
    return r.json()


# ── Master Patients ───────────────────────────────────────────────────────────
class TestMasterPatients:
    def test_create_patient(self, client):
        p = mk_patient(client)
        assert p["status"]    == "active"
        assert p["mrn"]       == "MRN-001"
        assert p["firstname"] == "Jane"

    def test_duplicate_mrn_rejected(self, client):
        mk_patient(client)
        r = client.post("/api/patients", json={
            "mrn": "MRN-001", "firstname": "Other", "lastname": "Person"
        })
        assert r.status_code == 409

    def test_list_patients(self, client):
        mk_patient(client, mrn="MRN-001")
        mk_patient(client, mrn="MRN-002", first="John", last="Smith")
        pts = client.get("/api/patients").json()
        assert len(pts) == 2

    def test_search_by_name(self, client):
        mk_patient(client, mrn="MRN-001")
        mk_patient(client, mrn="MRN-002", first="John", last="Smith")
        results = client.get("/api/patients?q=Smith").json()
        assert len(results) == 1
        assert results[0]["lastname"] == "Smith"

    def test_update_patient_demographics(self, client):
        p = mk_patient(client)
        r = client.patch(f"/api/patients/{p['id']}", json={"phone": "555-1234"})
        assert r.status_code == 200
        assert r.json()["phone"] == "555-1234"

    def test_get_patient_with_xrefs(self, client):
        p  = mk_patient(client)
        client.post("/api/crossref", json={
            "master_id": p["id"], "system": "ehr",
            "system_id": "P-TEST", "mrn": "MRN-001"
        })
        detail = client.get(f"/api/patients/{p['id']}").json()
        assert len(detail["cross_references"]) == 1
        assert detail["cross_references"][0]["system"] == "ehr"

    def test_cannot_update_merged_patient(self, client):
        a = mk_patient(client, mrn="MRN-A")
        b = mk_patient(client, mrn="MRN-B")
        client.post(f"/api/patients/{a['id']}/merge", json={"merge_id": b["id"]})
        r = client.patch(f"/api/patients/{b['id']}", json={"phone": "555-0000"})
        assert r.status_code == 409


# ── Lookup ────────────────────────────────────────────────────────────────────
class TestLookup:
    def test_lookup_by_mrn(self, client):
        mk_patient(client, mrn="MRN-999")
        r = client.get("/api/patients/lookup?mrn=MRN-999")
        assert r.status_code == 200
        assert r.json()["mrn"] == "MRN-999"

    def test_lookup_by_name_and_dob(self, client):
        mk_patient(client, first="Alice", last="Walker", dob="1990-07-04")
        r = client.get("/api/patients/lookup?firstname=Alice&lastname=Walker&birthdate=1990-07-04")
        assert r.status_code == 200
        assert r.json()["firstname"] == "Alice"

    def test_lookup_not_found(self, client):
        r = client.get("/api/patients/lookup?mrn=NOTEXIST")
        assert r.status_code == 404

    def test_lookup_by_crossref(self, client):
        p = mk_patient(client, mrn="MRN-XR")
        client.post("/api/crossref", json={
            "master_id": p["id"], "system": "lis", "system_id": "LIS-42"
        })
        r = client.get("/api/patients/lookup?system=lis&system_id=LIS-42")
        assert r.status_code == 200
        assert r.json()["mrn"] == "MRN-XR"


# ── Sync ──────────────────────────────────────────────────────────────────────
class TestSync:
    def test_sync_creates_master_and_xref(self, client):
        import time; time.sleep(0.05)  # allow background task
        r = client.post("/api/sync/from-ehr", json={
            "id": "EHR-1", "mrn": "MRN-SYNC", "firstname": "Tom",
            "lastname": "Hardy", "birthdate": "1977-09-15", "sex": "male"
        })
        assert r.status_code == 200
        time.sleep(0.1)
        pts = client.get("/api/patients?status=active").json()
        assert any(p["mrn"] == "MRN-SYNC" for p in pts)

    def test_sync_same_mrn_updates_xref(self, client):
        import time
        mk_patient(client, mrn="MRN-EXIST", first="Bob", last="Brown")
        r = client.post("/api/sync/from-ehr", json={
            "id": "EHR-99", "mrn": "MRN-EXIST",
            "firstname": "Bob", "lastname": "Brown", "birthdate": "1980-01-01"
        })
        assert r.status_code == 200
        time.sleep(0.1)
        pts = client.get("/api/patients?status=active").json()
        mrn_matches = [p for p in pts if p["mrn"] == "MRN-EXIST"]
        assert len(mrn_matches) == 1   # no duplicate


# ── Cross-References ──────────────────────────────────────────────────────────
class TestCrossReferences:
    def test_register_xref(self, client):
        p = mk_patient(client)
        r = client.post("/api/crossref", json={
            "master_id": p["id"], "system": "ris",
            "system_id": "RIS-77", "mrn": p["mrn"]
        })
        assert r.status_code == 201
        assert r.json()["system"] == "ris"

    def test_duplicate_xref_rejected(self, client):
        p = mk_patient(client)
        client.post("/api/crossref", json={
            "master_id": p["id"], "system": "ris", "system_id": "RIS-77"
        })
        r = client.post("/api/crossref", json={
            "master_id": p["id"], "system": "ris", "system_id": "RIS-77"
        })
        assert r.status_code == 409

    def test_delete_xref(self, client):
        p  = mk_patient(client)
        xr = client.post("/api/crossref", json={
            "master_id": p["id"], "system": "lis", "system_id": "LIS-5"
        }).json()
        r  = client.delete(f"/api/crossref/{xr['id']}")
        assert r.status_code == 204


# ── Matching Algorithm ────────────────────────────────────────────────────────
class TestMatchingAlgorithm:
    def test_exact_mrn_score_is_1(self):
        from matcher import compute_match_score
        a = {"id": "A", "mrn": "MRN-X", "firstname": "X", "lastname": "Y"}
        b = {"id": "B", "mrn": "MRN-X", "firstname": "P", "lastname": "Q"}
        assert compute_match_score(a, b) == 1.0

    def test_same_name_and_dob_high_score(self):
        from matcher import compute_match_score
        a = {"id": "A", "mrn": "MRN-1", "firstname": "John",
             "lastname": "Smith", "birthdate": "1990-01-01", "sex": "male"}
        b = {"id": "B", "mrn": "MRN-2", "firstname": "John",
             "lastname": "Smith", "birthdate": "1990-01-01", "sex": "male"}
        assert compute_match_score(a, b) >= 0.90

    def test_different_patients_low_score(self):
        from matcher import compute_match_score
        a = {"id": "A", "mrn": "MRN-1", "firstname": "Alice",
             "lastname": "Johnson", "birthdate": "1990-01-01"}
        b = {"id": "B", "mrn": "MRN-2", "firstname": "Bob",
             "lastname": "Williams", "birthdate": "1975-06-15"}
        assert compute_match_score(a, b) < 0.40

    def test_same_id_returns_1(self):
        from matcher import compute_match_score
        p = {"id": "SAME", "mrn": "MRN-1", "firstname": "X", "lastname": "Y"}
        assert compute_match_score(p, p) == 1.0

    def test_find_candidates_threshold(self):
        from matcher import find_candidates
        base   = {"id": "A", "mrn": "MRN-1", "firstname": "Jane",
                  "lastname": "Doe", "birthdate": "1985-03-15", "sex": "female"}
        twin   = {"id": "B", "mrn": "MRN-2", "firstname": "Jane",
                  "lastname": "Doe", "birthdate": "1985-03-15", "sex": "female"}
        diff   = {"id": "C", "mrn": "MRN-3", "firstname": "Bob",
                  "lastname": "Smith", "birthdate": "1970-01-01"}
        result = find_candidates(base, [base, twin, diff], threshold=0.70)
        ids    = [r[0]["id"] for r in result]
        assert "B" in ids
        assert "C" not in ids


# ── Merge ─────────────────────────────────────────────────────────────────────
class TestMerge:
    def test_merge_transfers_xrefs(self, client):
        a = mk_patient(client, mrn="MRN-A")
        b = mk_patient(client, mrn="MRN-B")
        client.post("/api/crossref", json={
            "master_id": b["id"], "system": "lis", "system_id": "LIS-B"
        })
        client.post(f"/api/patients/{a['id']}/merge",
                    json={"merge_id": b["id"], "performed_by": "Admin"})
        # b should be merged
        b_updated = client.get(f"/api/patients/{b['id']}").json()
        assert b_updated["status"] == "merged"
        # xref from b should now point to a
        xrefs = client.get(f"/api/crossref?master_id={a['id']}").json()
        assert any(x["system_id"] == "LIS-B" for x in xrefs)

    def test_cannot_merge_with_itself(self, client):
        a = mk_patient(client)
        r = client.post(f"/api/patients/{a['id']}/merge",
                        json={"merge_id": a["id"]})
        assert r.status_code == 400

    def test_merge_creates_audit_entry(self, client):
        a = mk_patient(client, mrn="MRN-AA")
        b = mk_patient(client, mrn="MRN-BB")
        client.post(f"/api/patients/{a['id']}/merge",
                    json={"merge_id": b["id"], "performed_by": "Tester"})
        audit = client.get(f"/api/audit?master_id={a['id']}").json()
        assert any(e["action"] == "merged" for e in audit)

    def test_audit_log_on_create(self, client):
        p = mk_patient(client)
        audit = client.get(f"/api/audit?master_id={p['id']}").json()
        assert any(e["action"] == "created" for e in audit)
