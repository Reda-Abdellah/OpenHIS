"""
Phase 3 — Clinical Notes / Documents Tests
Covers: CRUD, type validation, sign workflow, amendment chain,
        guard rails (edit/delete final), document upload/download/delete,
        FHIR Composition translator unit tests.
"""
import os, sys, io, tempfile, pytest

_EHR_DIR  = os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'ehr')
_FHIR_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'fhir-bridge')
sys.path.insert(0, os.path.abspath(_FHIR_DIR))
sys.path.insert(0, os.path.abspath(_EHR_DIR))

_TMP      = tempfile.mkdtemp()
_TESTDB   = os.path.join(_TMP, 'test_notes.db')
_DOCS_DIR = os.path.join(_TMP, 'docs')
os.makedirs(_DOCS_DIR, exist_ok=True)
os.environ['DB_PATH']          = _TESTDB
os.environ['DBPATH']           = _TESTDB
os.environ['DOCS_DIR']         = _DOCS_DIR
os.environ['FHIR_BRIDGE_URL']  = ''   # disable outbound calls

# Ensure we import from EHR, not a previously cached module
for _mod in list(sys.modules.keys()):
    if _mod in ('main', 'database') or _mod.startswith('routers.'):
        del sys.modules[_mod]

from fastapi.testclient import TestClient
from main import app
from database import init_db, get_db


def _seed_patient():
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO patients(id,mrn,first_name,last_name) VALUES(?,?,?,?)",
            ("P-TEST", "MRN-TEST", "Jane", "Test")
        )


@pytest.fixture(autouse=True)
def fresh_db():
    for p in [_TESTDB]:
        if os.path.exists(p): os.remove(p)
    init_db()
    _seed_patient()
    yield
    if os.path.exists(_TESTDB): os.remove(_TESTDB)


@pytest.fixture
def client(fresh_db):
    with TestClient(app) as c:
        yield c


# ── helpers ───────────────────────────────────────────────────────────────────
def mk_note(client, note_type="progress", content="Test content.",
            status="draft", title=None, author="Dr. Test"):
    r = client.post("/api/notes", json={
        "patient_id": "P-TEST",
        "note_type":  note_type,
        "content":    content,
        "author":     author,
        "title":      title,
        "status":     status,
    })
    assert r.status_code == 201, r.text
    return r.json()


def sign_note(client, note_id, author="Dr. Test"):
    r = client.post(f"/api/notes/{note_id}/sign",
                    json={"author": author})
    assert r.status_code == 200, r.text
    return r.json()


def upload_doc(client, patient_id="P-TEST", content=b"Hello PDF",
               filename="report.pdf", doc_type="attachment"):
    r = client.post(
        "/api/documents",
        data={"patient_id": patient_id, "doc_type": doc_type, "uploaded_by": "Nurse A"},
        files={"file": (filename, io.BytesIO(content), "application/pdf")}
    )
    assert r.status_code == 201, r.text
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Notes — CRUD
# ─────────────────────────────────────────────────────────────────────────────
class TestNotesCRUD:
    def test_create_draft_note(self, client):
        note = mk_note(client)
        assert note["status"]     == "draft"
        assert note["patient_id"] == "P-TEST"
        assert note["note_type"]  == "progress"

    def test_list_notes_by_patient(self, client):
        mk_note(client, content="First")
        mk_note(client, content="Second")
        notes = client.get("/api/notes?patient_id=P-TEST").json()
        assert len(notes) == 2

    def test_list_notes_filter_by_type(self, client):
        mk_note(client, note_type="progress")
        mk_note(client, note_type="nursing")
        notes = client.get("/api/notes?patient_id=P-TEST&note_type=nursing").json()
        assert len(notes) == 1
        assert notes[0]["note_type"] == "nursing"

    def test_get_single_note(self, client):
        note = mk_note(client, title="My Note")
        r    = client.get(f"/api/notes/{note['id']}")
        assert r.status_code == 200
        assert r.json()["title"] == "My Note"

    def test_update_draft_note(self, client):
        note = mk_note(client)
        r    = client.patch(f"/api/notes/{note['id']}",
                            json={"content": "Updated content.", "title": "Updated"})
        assert r.status_code == 200
        assert r.json()["content"] == "Updated content."

    def test_invalid_note_type_rejected(self, client):
        r = client.post("/api/notes", json={
            "patient_id": "P-TEST", "note_type": "invalid_type",
            "content": "X", "status": "draft"
        })
        assert r.status_code == 422

    def test_delete_draft_note(self, client):
        note = mk_note(client)
        r    = client.delete(f"/api/notes/{note['id']}")
        assert r.status_code == 204
        assert client.get(f"/api/notes/{note['id']}").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Notes — Sign workflow
# ─────────────────────────────────────────────────────────────────────────────
class TestNoteSign:
    def test_sign_draft_becomes_final(self, client):
        note = mk_note(client)
        vn   = sign_note(client, note["id"])
        assert vn["status"]    == "final"
        assert vn["signed_at"] is not None
        assert vn["author"]    == "Dr. Test"

    def test_cannot_sign_already_final(self, client):
        note = mk_note(client)
        sign_note(client, note["id"])
        r = client.post(f"/api/notes/{note['id']}/sign", json={"author": "X"})
        assert r.status_code == 409

    def test_cannot_edit_final_note(self, client):
        note = mk_note(client)
        sign_note(client, note["id"])
        r = client.patch(f"/api/notes/{note['id']}", json={"content": "Tampered"})
        assert r.status_code == 409

    def test_cannot_delete_final_note(self, client):
        note = mk_note(client)
        sign_note(client, note["id"])
        r = client.delete(f"/api/notes/{note['id']}")
        assert r.status_code == 409

    def test_list_filter_by_status(self, client):
        mk_note(client, content="Draft one")
        n2 = mk_note(client, content="Final one")
        sign_note(client, n2["id"])
        finals = client.get("/api/notes?patient_id=P-TEST&status=final").json()
        assert all(n["status"] == "final" for n in finals)
        assert len(finals) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Notes — Amendment chain
# ─────────────────────────────────────────────────────────────────────────────
class TestNoteAmendment:
    def test_amend_final_note(self, client):
        note = mk_note(client, content="Original text.")
        sign_note(client, note["id"])
        r = client.post(f"/api/notes/{note['id']}/amend", json={
            "content":          "Corrected text.",
            "amendment_reason": "Transcription error",
            "author":           "Dr. Test"
        })
        assert r.status_code == 201
        amend = r.json()
        assert amend["status"]       == "draft"
        assert amend["amended_from"] == note["id"]
        assert "AMENDMENT" in amend["title"]

    def test_original_marked_amended(self, client):
        note = mk_note(client)
        sign_note(client, note["id"])
        client.post(f"/api/notes/{note['id']}/amend",
                    json={"content": "Correction.", "amendment_reason": "Error"})
        orig = client.get(f"/api/notes/{note['id']}").json()
        assert orig["status"] == "amended"

    def test_cannot_amend_draft(self, client):
        note = mk_note(client)   # still draft
        r    = client.post(f"/api/notes/{note['id']}/amend",
                           json={"content": "X", "amendment_reason": "Y"})
        assert r.status_code == 409

    def test_amendment_can_be_signed(self, client):
        note = mk_note(client)
        sign_note(client, note["id"])
        amend_r = client.post(f"/api/notes/{note['id']}/amend",
                              json={"content": "Fixed.", "amendment_reason": "Err"})
        amend = amend_r.json()
        final = sign_note(client, amend["id"], author="Dr. Test")
        assert final["status"] == "final"


# ─────────────────────────────────────────────────────────────────────────────
# Documents — Upload / Download / Delete
# ─────────────────────────────────────────────────────────────────────────────
class TestDocuments:
    def test_upload_document(self, client):
        doc = upload_doc(client, content=b"%PDF-test-content")
        assert doc["original_name"]  == "report.pdf"
        assert doc["patient_id"]     == "P-TEST"
        assert doc["file_size"]       > 0

    def test_list_documents_by_patient(self, client):
        upload_doc(client, filename="doc1.pdf")
        upload_doc(client, filename="doc2.pdf")
        docs = client.get("/api/documents?patient_id=P-TEST").json()
        assert len(docs) == 2

    def test_download_document(self, client):
        payload = b"REPORT CONTENT 12345"
        doc     = upload_doc(client, content=payload, filename="test.txt")
        r       = client.get(f"/api/documents/{doc['id']}/download")
        assert r.status_code  == 200
        assert r.content       == payload

    def test_delete_document(self, client):
        doc = upload_doc(client)
        r   = client.delete(f"/api/documents/{doc['id']}")
        assert r.status_code == 204
        assert client.get(f"/api/documents/{doc['id']}").status_code == 404

    def test_document_linked_to_note(self, client):
        note = mk_note(client)
        r = client.post(
            "/api/documents",
            data={"patient_id": "P-TEST", "note_id": str(note["id"]),
                  "doc_type": "attachment"},
            files={"file": ("attach.pdf", io.BytesIO(b"data"), "application/pdf")}
        )
        assert r.status_code  == 201
        assert r.json()["note_id"] == note["id"]
        # Filter by note
        docs_for_note = client.get(f"/api/documents?note_id={note['id']}").json()
        assert len(docs_for_note) == 1


# ─────────────────────────────────────────────────────────────────────────────
# FHIR Composition Translator — unit tests (no DB needed)
# ─────────────────────────────────────────────────────────────────────────────
class TestFHIRComposition:
    def test_composition_resource_type(self):
        from translators.composition import to_fhir_composition
        note = {
            "id": 1, "patient_id": "P-TEST", "note_type": "progress",
            "status": "final", "content": "Patient stable.",
            "author": "Dr. Smith", "signed_at": "2026-03-23T22:00:00"
        }
        fhir = to_fhir_composition(note)
        assert fhir["resourceType"] == "Composition"
        assert fhir["status"]       == "final"
        assert fhir["id"]           == "note-1"

    def test_loinc_code_mapping(self):
        from translators.composition import to_fhir_composition
        for note_type, expected_code in [
            ("discharge",    "18842-5"),
            ("consultation", "11488-4"),
            ("procedure",    "28570-0"),
            ("nursing",      "46240-8"),
        ]:
            note = {"id": 1, "patient_id": "P-TEST", "note_type": note_type,
                    "status": "final", "content": "X", "author": "Dr. A"}
            fhir = to_fhir_composition(note)
            assert fhir["type"]["coding"][0]["code"] == expected_code, \
                f"Wrong LOINC for {note_type}"

    def test_draft_maps_to_preliminary(self):
        from translators.composition import to_fhir_composition
        note = {"id": 2, "patient_id": "P-TEST", "note_type": "progress",
                "status": "draft", "content": "Draft.", "author": "Dr. B"}
        fhir = to_fhir_composition(note)
        assert fhir["status"] == "preliminary"

    def test_amended_status_mapped(self):
        from translators.composition import to_fhir_composition
        note = {"id": 3, "patient_id": "P-TEST", "note_type": "soap",
                "status": "amended", "content": "Old.", "author": "Dr. C"}
        fhir = to_fhir_composition(note)
        assert fhir["status"] == "amended"

    def test_content_xss_escaped(self):
        from translators.composition import to_fhir_composition
        note = {"id": 4, "patient_id": "P-TEST", "note_type": "progress",
                "status": "final", "content": "<script>alert('xss')</script>",
                "author": "Dr. D"}
        fhir = to_fhir_composition(note)
        div  = fhir["section"][0]["text"]["div"]
        assert "<script>" not in div
        assert "&lt;script&gt;" in div
