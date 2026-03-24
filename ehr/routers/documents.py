import os, uuid, mimetypes
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router   = APIRouter(prefix="/api/documents", tags=["documents"])
DOCS_DIR = os.environ.get('DOCS_DIR', 'data/documents')
MAX_SIZE = 20 * 1024 * 1024   # 20 MB


@router.get("")
def list_documents(patient_id: Optional[str] = None, note_id: Optional[int] = None):
    clauses, params = [], []
    if patient_id: clauses.append("patient_id=?"); params.append(patient_id)
    if note_id:    clauses.append("note_id=?");    params.append(note_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM note_documents {where} ORDER BY createdat DESC", params
        ).fetchall())


@router.get("/{doc_id}")
def get_document(doc_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM note_documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        return dict(row)


@router.get("/{doc_id}/download")
def download_document(doc_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM note_documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        doc = dict(row)
    filepath = os.path.join(DOCS_DIR, doc["filename"])
    if not os.path.exists(filepath):
        raise HTTPException(404, "File not found on disk")
    return FileResponse(
        path=filepath,
        filename=doc["original_name"],
        media_type=doc["mime_type"]
    )


@router.post("", status_code=201)
async def upload_document(
    patient_id:   str            = Form(...),
    encounter_id: Optional[int]  = Form(None),
    note_id:      Optional[int]  = Form(None),
    doc_type:     str            = Form("attachment"),
    description:  Optional[str] = Form(None),
    uploaded_by:  Optional[str] = Form(None),
    file:         UploadFile     = File(...)
):
    ext        = os.path.splitext(file.filename or "file.bin")[1].lower() or ".bin"
    unique     = f"{uuid.uuid4().hex}{ext}"
    dest_dir   = os.path.join(DOCS_DIR, patient_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path  = os.path.join(dest_dir, unique)
    size = 0
    with open(dest_path, "wb") as fout:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_SIZE:
                fout.close()
                os.remove(dest_path)
                raise HTTPException(413, "File too large (max 20 MB)")
            fout.write(chunk)
    mime     = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    rel_path = f"{patient_id}/{unique}"
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO note_documents"
            "(patient_id,encounter_id,note_id,filename,original_name,"
            "mime_type,file_size,description,doc_type,uploaded_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (patient_id, encounter_id, note_id, rel_path,
             file.filename or "file", mime, size,
             description, doc_type, uploaded_by)
        )
        return row_to_dict(db.execute("SELECT * FROM note_documents WHERE id=?", (cur.lastrowid,)).fetchone())


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM note_documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        doc = dict(row)
    filepath = os.path.join(DOCS_DIR, doc["filename"])
    if os.path.exists(filepath):
        os.remove(filepath)
    with get_db() as db:
        db.execute("DELETE FROM note_documents WHERE id=?", (doc_id,))
