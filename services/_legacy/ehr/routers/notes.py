import datetime, os
import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/notes", tags=["notes"])
FHIR_BRIDGE_URL = os.environ.get('FHIR_BRIDGE_URL', '')
NOTE_SQL = "SELECT * FROM clinical_notes"


class NoteCreate(BaseModel):
    patient_id:   str
    encounter_id: Optional[int] = None
    note_type:    Optional[str] = "progress"
    title:        Optional[str] = None
    content:      str
    author:       Optional[str] = None
    status:       Optional[str] = "draft"   # draft | final


class NoteUpdate(BaseModel):
    title:        Optional[str] = None
    content:      Optional[str] = None
    note_type:    Optional[str] = None
    author:       Optional[str] = None


@router.get("")
def list_notes(
    patient_id:   Optional[str] = None,
    encounter_id: Optional[int] = None,
    note_type:    Optional[str] = None,
    status:       Optional[str] = None,
):
    clauses, params = [], []
    if patient_id:   clauses.append("patient_id=?");   params.append(patient_id)
    if encounter_id: clauses.append("encounter_id=?"); params.append(encounter_id)
    if note_type:    clauses.append("note_type=?");    params.append(note_type)
    if status:       clauses.append("status=?");       params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"{NOTE_SQL} {where} ORDER BY createdat DESC", params
        ).fetchall())


@router.get("/{note_id}")
def get_note(note_id: int):
    with get_db() as db:
        row = db.execute(f"{NOTE_SQL} WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
        return dict(row)


@router.post("", status_code=201)
def create_note(body: NoteCreate):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    valid_types = {"progress", "soap", "nursing", "discharge", "consultation", "procedure"}
    if body.note_type and body.note_type not in valid_types:
        raise HTTPException(422, f"note_type must be one of: {', '.join(sorted(valid_types))}")
    with get_db() as db:
        if not db.execute("SELECT 1 FROM patients WHERE id=?", (body.patient_id,)).fetchone():
            raise HTTPException(404, "Patient not found")
        signed_at = now if body.status == "final" else None
        cur = db.execute(
            "INSERT INTO clinical_notes"
            "(patient_id,encounter_id,note_type,title,content,status,author,signed_at,createdat,updatedat) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (body.patient_id, body.encounter_id, body.note_type or "progress",
             body.title, body.content, body.status or "draft",
             body.author, signed_at, now, now)
        )
        return row_to_dict(db.execute(f"{NOTE_SQL} WHERE id=?", (cur.lastrowid,)).fetchone())


@router.patch("/{note_id}")
def update_note(note_id: int, body: NoteUpdate):
    with get_db() as db:
        row = db.execute("SELECT status FROM clinical_notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
        if dict(row)["status"] != "draft":
            raise HTTPException(409, "Only draft notes can be edited")
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(400, "No valid fields to update")
        updates["updatedat"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE clinical_notes SET {sets} WHERE id=?", (*updates.values(), note_id))
        return row_to_dict(db.execute(f"{NOTE_SQL} WHERE id=?", (note_id,)).fetchone())


@router.post("/{note_id}/sign")
async def sign_note(note_id: int, body: dict, bg: BackgroundTasks):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        row = db.execute(f"{NOTE_SQL} WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
        note = dict(row)
        if note["status"] == "final":
            raise HTTPException(409, "Note already finalized")
        if note["status"] == "amended":
            raise HTTPException(409, "Cannot sign an amended note; create a new amendment draft")
        author = body.get("author") or note.get("author") or "Unknown"
        db.execute(
            "UPDATE clinical_notes SET status='final', author=?, signed_at=?, updatedat=? WHERE id=?",
            (author, now, now, note_id)
        )
        updated = row_to_dict(db.execute(f"{NOTE_SQL} WHERE id=?", (note_id,)).fetchone())
    if FHIR_BRIDGE_URL:
        bg.add_task(_notify_fhir_note, updated)
    return updated


@router.post("/{note_id}/amend", status_code=201)
def amend_note(note_id: int, body: dict):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        orig = db.execute(f"{NOTE_SQL} WHERE id=?", (note_id,)).fetchone()
        if not orig:
            raise HTTPException(404, "Note not found")
        orig = dict(orig)
        if orig["status"] != "final":
            raise HTTPException(409, "Only final notes can be amended")
        content = body.get("content")
        if not content:
            raise HTTPException(400, "Amendment content required")
        db.execute("UPDATE clinical_notes SET status='amended', updatedat=? WHERE id=?", (now, note_id))
        cur = db.execute(
            "INSERT INTO clinical_notes"
            "(patient_id,encounter_id,note_type,title,content,status,"
            "author,amended_from,amendment_reason,createdat,updatedat) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (orig["patient_id"], orig.get("encounter_id"), orig["note_type"],
             f"[AMENDMENT] {orig.get('title') or orig['note_type']}",
             content, "draft",
             body.get("author") or orig.get("author"),
             note_id, body.get("amendment_reason"), now, now)
        )
        return row_to_dict(db.execute(f"{NOTE_SQL} WHERE id=?", (cur.lastrowid,)).fetchone())


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int):
    with get_db() as db:
        row = db.execute("SELECT status FROM clinical_notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
        if dict(row)["status"] != "draft":
            raise HTTPException(409, "Only draft notes can be deleted")
        db.execute("DELETE FROM clinical_notes WHERE id=?", (note_id,))


async def _notify_fhir_note(note: dict):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"{FHIR_BRIDGE_URL}/api/events/note-finalized", json=note)
    except Exception:
        pass
