"""
Inbound sync endpoints — receive patient demographics from each service,
upsert master record, register cross-reference, run matching.
"""
import datetime
from fastapi import APIRouter, BackgroundTasks
from database import get_db, rows_to_list, row_to_dict, new_id
from matcher import find_candidates

router = APIRouter(prefix="/api/sync", tags=["sync"])
THRESHOLD = 0.70


def _upsert_and_xref(payload: dict, system: str, system_id_key: str = "id"):
    """Core logic: upsert master patient + cross-reference."""
    now    = datetime.datetime.utcnow().isoformat(timespec="seconds")
    s_id   = str(payload.get(system_id_key, ""))
    mrn    = (payload.get("mrn") or "").strip()
    master = None

    with get_db() as db:
        # ── 1. Look for existing master by MRN ───────────────────────────────
        if mrn:
            row = db.execute(
                "SELECT * FROM master_patients WHERE mrn=?", (mrn,)
            ).fetchone()
            if row:
                master = dict(row)

        # ── 2. Look for existing cross-ref ───────────────────────────────────
        if not master and s_id:
            xref = db.execute(
                "SELECT master_id FROM cross_references WHERE system=? AND system_id=?",
                (system, s_id)
            ).fetchone()
            if xref:
                row = db.execute(
                    "SELECT * FROM master_patients WHERE id=?",
                    (xref["master_id"],)
                ).fetchone()
                if row:
                    master = dict(row)

        # ── 3. Create new master if none found ───────────────────────────────
        if not master:
            if not mrn:
                return {"status": "skipped", "reason": "no MRN"}
            pid = new_id()
            db.execute(
                "INSERT INTO master_patients"
                "(id,mrn,firstname,lastname,birthdate,sex,phone,insurance_id,createdat,updatedat) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, mrn,
                 payload.get("firstname") or "",
                 payload.get("lastname") or "",
                 payload.get("birthdate"), payload.get("sex"),
                 payload.get("phone"), payload.get("insuranceid"),
                 now, now)
            )
            db.execute(
                "INSERT INTO audit_log(master_id,action,details) VALUES(?,?,?)",
                (pid, f"created-from-{system}", f"MRN={mrn}")
            )
            master = row_to_dict(db.execute(
                "SELECT * FROM master_patients WHERE id=?", (pid,)
            ).fetchone())
        else:
            # ── 4. Update demographics (non-destructive) ─────────────────────
            fields = {"firstname": payload.get("firstname"),
                      "lastname":  payload.get("lastname"),
                      "birthdate": payload.get("birthdate"),
                      "sex":       payload.get("sex"),
                      "phone":     payload.get("phone")}
            updates = {k: v for k, v in fields.items()
                       if v and not master.get(k)}
            if updates:
                updates["updatedat"] = now
                sets = ", ".join(f"{k}=?" for k in updates)
                db.execute(
                    f"UPDATE master_patients SET {sets} WHERE id=?",
                    (*updates.values(), master["id"])
                )

        # ── 5. Upsert cross-reference ─────────────────────────────────────────
        if s_id:
            try:
                db.execute(
                    "INSERT INTO cross_references"
                    "(master_id,system,system_id,mrn) VALUES(?,?,?,?)",
                    (master["id"], system, s_id, mrn)
                )
            except Exception:
                pass   # already exists

        # ── 6. Run matching against existing actives ─────────────────────────
        all_patients = rows_to_list(db.execute(
            "SELECT * FROM master_patients WHERE status='active'"
        ).fetchall())
        master_fresh = row_to_dict(db.execute(
            "SELECT * FROM master_patients WHERE id=?", (master["id"],)
        ).fetchone())
        hits = find_candidates(master_fresh, all_patients, THRESHOLD)
        for (candidate, score) in hits:
            a_id = min(master["id"], candidate["id"])
            b_id = max(master["id"], candidate["id"])
            try:
                db.execute(
                    "INSERT INTO match_candidates(master_id_a,master_id_b,score) VALUES(?,?,?)",
                    (a_id, b_id, score)
                )
            except Exception:
                pass

    return {"status": "ok", "master_id": master["id"], "mrn": mrn}


@router.post("/from-ehr")
async def sync_from_ehr(payload: dict, bg: BackgroundTasks):
    bg.add_task(_upsert_and_xref, payload, "ehr")
    return {"status": "queued", "system": "ehr"}


@router.post("/from-lis")
async def sync_from_lis(payload: dict, bg: BackgroundTasks):
    bg.add_task(_upsert_and_xref, payload, "lis", "ehrpatientid")
    return {"status": "queued", "system": "lis"}


@router.post("/from-ris")
async def sync_from_ris(payload: dict, bg: BackgroundTasks):
    bg.add_task(_upsert_and_xref, payload, "ris", "ehrid")
    return {"status": "queued", "system": "ris"}
