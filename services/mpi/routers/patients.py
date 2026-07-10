import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict, new_id
from jwt_auth import JWTMiddleware  # noqa: F401 — imported for side-effect
from openhis_sdk.auth import require_roles
import bus

log = logging.getLogger("mpi.patients")

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _publish_patient_synced(master_id: str, mrn: str) -> None:
    """Best-effort patient.synced emit after a committed REST mutation (DEF-010).

    Called AFTER the `with get_db()` block exits, so consumers can never
    observe an event for an uncommitted (or rolled-back) row. bus.publish
    already swallows Redis errors; the extra guard makes the invariant
    structural — a publish failure must NEVER fail the API request.
    """
    try:
        bus.publish("patient.synced", {
            "master_id": master_id,
            "mrn":       mrn,
            "source":    "mpi",
        })
    except Exception:
        log.warning(
            "patient.synced publish failed",
            extra={"master_id": master_id, "mrn": mrn},
            exc_info=True,
        )

_MP_SQL = """
    SELECT m.*,
           (SELECT string_agg(cr.system || ':' || cr.system_id, ',')
            FROM cross_references cr WHERE cr.master_id = m.id) AS xrefs,
           (SELECT COUNT(*) FROM match_candidates mc
            WHERE (mc.master_id_a = m.id OR mc.master_id_b = m.id)
            AND mc.status = 'pending') AS pending_matches
    FROM master_patients m
"""


class PatientCreate(BaseModel):
    mrn:         str
    firstname:   str
    lastname:    str
    birthdate:   Optional[str] = None
    sex:         Optional[str] = None
    phone:       Optional[str] = None
    address:     Optional[str] = None
    insurance_id: Optional[str] = None


class PatientUpdate(BaseModel):
    firstname:   Optional[str] = None
    lastname:    Optional[str] = None
    birthdate:   Optional[str] = None
    sex:         Optional[str] = None
    phone:       Optional[str] = None
    address:     Optional[str] = None
    insurance_id: Optional[str] = None


@router.get("", dependencies=[Depends(require_roles("clinician", "radiologist", "lab-tech", "admin"))])
def list_patients(q: Optional[str] = None, status: Optional[str] = "active"):
    clauses, params = [], []
    if status:
        clauses.append("m.status=?"); params.append(status)
    if q:
        like = f"%{q}%"
        clauses.append(
            "(m.firstname LIKE ? OR m.lastname LIKE ? OR m.mrn LIKE ?)"
        )
        params += [like, like, like]
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"{_MP_SQL} {where} ORDER BY m.lastname, m.firstname", params
        ).fetchall())


@router.get("/lookup", dependencies=[Depends(require_roles("clinician", "radiologist", "lab-tech", "admin"))])
def lookup(
    mrn:          Optional[str] = None,
    system:       Optional[str] = None,
    system_id:    Optional[str] = None,
    firstname:    Optional[str] = None,
    lastname:     Optional[str] = None,
    birthdate:    Optional[str] = None,
):
    """Multi-criteria patient lookup — returns first strong match."""
    with get_db() as db:
        # 1. MRN direct lookup
        if mrn:
            row = db.execute(
                f"{_MP_SQL} WHERE m.mrn=? AND m.status='active'", (mrn,)
            ).fetchone()
            if row:
                return row_to_dict(row)

        # 2. Cross-reference lookup
        if system and system_id:
            xref = db.execute(
                "SELECT master_id FROM cross_references WHERE system=? AND system_id=?",
                (system, system_id)
            ).fetchone()
            if xref:
                row = db.execute(
                    f"{_MP_SQL} WHERE m.id=?", (xref["master_id"],)
                ).fetchone()
                if row:
                    return row_to_dict(row)

        # 3. Name + birthdate lookup
        if firstname and lastname and birthdate:
            rows = rows_to_list(db.execute(
                f"{_MP_SQL} WHERE m.firstname LIKE ? AND m.lastname LIKE ? "
                "AND m.birthdate=? AND m.status='active'",
                (f"%{firstname}%", f"%{lastname}%", birthdate)
            ).fetchall())
            if rows:
                return rows[0]

    raise HTTPException(404, "No matching patient found")


@router.get("/{pid}", dependencies=[Depends(require_roles(
    "clinician", "radiologist", "lab-tech", "admin",
    # internal-sync: the integration-hub's patient.synced consumer resolves
    # the master record before pushing it to OpenELIS (DEF-010).
    "internal-sync"))])
def get_patient(pid: str):
    with get_db() as db:
        row = db.execute(f"{_MP_SQL} WHERE m.id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        p = dict(row)
        p["cross_references"] = rows_to_list(db.execute(
            "SELECT * FROM cross_references WHERE master_id=?", (pid,)
        ).fetchall())
        p["audit"] = rows_to_list(db.execute(
            "SELECT * FROM audit_log WHERE master_id=? ORDER BY createdat DESC LIMIT 20",
            (pid,)
        ).fetchall())
    return p


@router.post("", status_code=201, dependencies=[Depends(require_roles("clinician", "admin"))])
def create_patient(body: PatientCreate):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    pid = new_id()
    with get_db() as db:
        if db.execute(
            "SELECT 1 FROM master_patients WHERE mrn=?", (body.mrn,)
        ).fetchone():
            raise HTTPException(409, f"MRN {body.mrn!r} already registered")
        db.execute(
            "INSERT INTO master_patients"
            "(id,mrn,firstname,lastname,birthdate,sex,phone,address,insurance_id,createdat,updatedat) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (pid, body.mrn, body.firstname, body.lastname,
             body.birthdate, body.sex, body.phone,
             body.address, body.insurance_id, now, now)
        )
        db.execute(
            "INSERT INTO audit_log(master_id,action,details) VALUES(?,?,?)",
            (pid, "created", f"MRN={body.mrn}")
        )
        created = row_to_dict(db.execute("SELECT * FROM master_patients WHERE id=?", (pid,)).fetchone())
    # Transaction committed on `with` exit — publish only for persisted rows.
    _publish_patient_synced(pid, body.mrn)
    return created


@router.patch("/{pid}", dependencies=[Depends(require_roles("clinician", "admin"))])
def update_patient(pid: str, body: PatientUpdate):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        row = db.execute(
            "SELECT status FROM master_patients WHERE id=?", (pid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        if dict(row)["status"] in ("merged",):
            raise HTTPException(409, "Cannot update a merged record")
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(400, "No valid fields")
        updates["updatedat"] = now
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(
            f"UPDATE master_patients SET {sets} WHERE id=?",
            (*updates.values(), pid)
        )
        db.execute(
            "INSERT INTO audit_log(master_id,action,details) VALUES(?,?,?)",
            (pid, "updated", ", ".join(updates.keys()))
        )
        updated = row_to_dict(db.execute("SELECT * FROM master_patients WHERE id=?", (pid,)).fetchone())
    # Transaction committed on `with` exit — publish only for persisted rows.
    _publish_patient_synced(pid, updated["mrn"])
    return updated


@router.post("/{pid}/merge", dependencies=[Depends(require_roles("admin"))])
def merge_patients(pid: str, body: dict):
    """Merge `body.merge_id` into `pid` (pid is the surviving record)."""
    merge_id = body.get("merge_id")
    if not merge_id:
        raise HTTPException(400, "merge_id required")
    if merge_id == pid:
        raise HTTPException(400, "Cannot merge a patient with itself")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        surviving = db.execute(
            "SELECT * FROM master_patients WHERE id=? AND status='active'", (pid,)
        ).fetchone()
        to_merge  = db.execute(
            "SELECT * FROM master_patients WHERE id=? AND status='active'", (merge_id,)
        ).fetchone()
        if not surviving:
            raise HTTPException(404, f"Surviving patient {pid!r} not found or not active")
        if not to_merge:
            raise HTTPException(404, f"Patient to merge {merge_id!r} not found or not active")

        # Transfer cross-refs to surviving patient
        xrefs = rows_to_list(db.execute(
            "SELECT * FROM cross_references WHERE master_id=?", (merge_id,)
        ).fetchall())
        for xr in xrefs:
            conflict = db.execute(
                "SELECT 1 FROM cross_references WHERE master_id=? AND system=? AND system_id=?",
                (pid, xr["system"], xr["system_id"])
            ).fetchone()
            if conflict:
                db.execute("DELETE FROM cross_references WHERE id=?", (xr["id"],))
            else:
                db.execute(
                    "UPDATE cross_references SET master_id=? WHERE id=?",
                    (pid, xr["id"])
                )

        # Mark merged record
        db.execute(
            "UPDATE master_patients SET status='merged', merged_into=?, updatedat=? WHERE id=?",
            (pid, now, merge_id)
        )
        # Resolve any pending match between these two
        db.execute(
            "UPDATE match_candidates SET status='confirmed_match', reviewedat=? "
            "WHERE (master_id_a=? AND master_id_b=?) OR (master_id_a=? AND master_id_b=?)",
            (now, pid, merge_id, merge_id, pid)
        )
        db.execute(
            "INSERT INTO audit_log(master_id,action,performed_by,details) VALUES(?,?,?,?)",
            (pid, "merged", body.get("performed_by", "system"),
             f"merged {merge_id} into {pid}; transferred {len(xrefs)} xrefs")
        )
        merged = row_to_dict(db.execute(
            f"{_MP_SQL} WHERE m.id=?", (pid,)
        ).fetchone())
    # Transaction committed on `with` exit — surviving record is the master.
    _publish_patient_synced(pid, dict(surviving)["mrn"])
    return merged
