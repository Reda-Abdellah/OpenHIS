from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict
from openhis_sdk.auth import require_token

router = APIRouter(prefix="/api/crossref", tags=["crossref"])


class XRefCreate(BaseModel):
    master_id:           str
    system:              str
    system_id:           str
    mrn:                 Optional[str] = None
    assigning_authority: Optional[str] = None


@router.get("", dependencies=[Depends(require_token)])
def list_xrefs(master_id: Optional[str] = None, system: Optional[str] = None):
    clauses, params = [], []
    if master_id: clauses.append("master_id=?"); params.append(master_id)
    if system:    clauses.append("system=?");    params.append(system)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM cross_references {where} ORDER BY createdat DESC", params
        ).fetchall())


@router.post("", status_code=201, dependencies=[Depends(require_token)])
def create_xref(body: XRefCreate):
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM master_patients WHERE id=?", (body.master_id,)
        ).fetchone():
            raise HTTPException(404, "Master patient not found")
        try:
            cur = db.execute(
                "INSERT INTO cross_references"
                "(master_id,system,system_id,mrn,assigning_authority) VALUES(?,?,?,?,?)",
                (body.master_id, body.system, body.system_id,
                 body.mrn, body.assigning_authority)
            )
            return row_to_dict(db.execute(
                "SELECT * FROM cross_references WHERE id=?", (cur.lastrowid,)
            ).fetchone())
        except Exception:
            raise HTTPException(409,
                f"Cross-reference ({body.system}:{body.system_id}) already registered")


@router.delete("/{xref_id}", status_code=204, dependencies=[Depends(require_token)])
def delete_xref(xref_id: int):
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM cross_references WHERE id=?", (xref_id,)
        ).fetchone():
            raise HTTPException(404, "Cross-reference not found")
        db.execute("DELETE FROM cross_references WHERE id=?", (xref_id,))
