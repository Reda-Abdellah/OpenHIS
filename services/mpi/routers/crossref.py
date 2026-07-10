import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict
from openhis_sdk.auth import require_token
import bus

log = logging.getLogger("mpi.crossref")

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
                "(master_id,system,system_id,mrn,assigning_authority) VALUES(?,?,?,?,?)"
                " RETURNING id",
                (body.master_id, body.system, body.system_id,
                 body.mrn, body.assigning_authority)
            )
            xref_id = cur.fetchone()["id"]
            created = row_to_dict(db.execute(
                "SELECT * FROM cross_references WHERE id=?", (xref_id,)
            ).fetchone())
        except Exception:
            raise HTTPException(409,
                f"Cross-reference ({body.system}:{body.system_id}) already registered")
    # A new cross-reference changes the patient's downstream identity —
    # re-emit patient.synced so the hub re-upserts with the full identifier
    # set (DEF-010). Fire-and-forget: publish failures never fail the API.
    try:
        mrn = body.mrn
        if not mrn:
            with get_db() as db:
                row = db.execute(
                    "SELECT mrn FROM master_patients WHERE id=?", (body.master_id,)
                ).fetchone()
                mrn = row["mrn"] if row else ""
        bus.publish("patient.synced", {
            "master_id": body.master_id,
            "mrn":       mrn or "",
            "source":    "mpi",
        })
    except Exception:
        log.warning("patient.synced publish failed after xref create",
                    extra={"master_id": body.master_id}, exc_info=True)
    return created


@router.delete("/{xref_id}", status_code=204, dependencies=[Depends(require_token)])
def delete_xref(xref_id: int):
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM cross_references WHERE id=?", (xref_id,)
        ).fetchone():
            raise HTTPException(404, "Cross-reference not found")
        db.execute("DELETE FROM cross_references WHERE id=?", (xref_id,))
