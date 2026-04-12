import datetime
from fastapi import APIRouter, Depends, HTTPException
from database import get_db, rows_to_list, row_to_dict
from matcher import find_candidates
from openhis_sdk.auth import require_token

router = APIRouter(prefix="/api/matching", tags=["matching"])

_THRESHOLD = 0.70


@router.get("/candidates", dependencies=[Depends(require_token)])
def list_candidates(status: str = "pending"):
    with get_db() as db:
        rows = db.execute(
            """SELECT mc.*,
                      a.firstname || ' ' || a.lastname AS name_a, a.mrn AS mrn_a,
                      b.firstname || ' ' || b.lastname AS name_b, b.mrn AS mrn_b
               FROM match_candidates mc
               JOIN master_patients a ON a.id = mc.master_id_a
               JOIN master_patients b ON b.id = mc.master_id_b
               WHERE mc.status = ?
               ORDER BY mc.score DESC, mc.createdat DESC""",
            (status,)
        ).fetchall()
    return rows_to_list(rows)


@router.post("/run", dependencies=[Depends(require_token)])
def run_matching():
    """Scan all active patients and create match_candidates above threshold."""
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        patients = rows_to_list(db.execute(
            "SELECT * FROM master_patients WHERE status='active'"
        ).fetchall())
        created = 0
        for p in patients:
            hits = find_candidates(p, patients, threshold=_THRESHOLD)
            for (candidate, score) in hits:
                a_id = min(p["id"], candidate["id"])
                b_id = max(p["id"], candidate["id"])
                cur = db.execute(
                    "INSERT INTO match_candidates(master_id_a,master_id_b,score) VALUES(?,?,?)"
                    " ON CONFLICT DO NOTHING",
                    (a_id, b_id, score)
                )
                created += cur.rowcount
    return {"candidates_created": created, "patients_scanned": len(patients)}


@router.post("/candidates/{cid}/resolve", dependencies=[Depends(require_token)])
def resolve_candidate(cid: int, body: dict):
    """
    Resolve a match candidate.
    body.decision: 'confirmed_match' | 'confirmed_no_match'
    body.reviewed_by: str
    For 'confirmed_match', also supply surviving_id to trigger merge.
    """
    decision    = body.get("decision")
    reviewed_by = body.get("reviewed_by", "reviewer")
    if decision not in ("confirmed_match", "confirmed_no_match"):
        raise HTTPException(400, "decision must be 'confirmed_match' or 'confirmed_no_match'")
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        row = db.execute("SELECT * FROM match_candidates WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Candidate not found")
        db.execute(
            "UPDATE match_candidates SET status=?, reviewed_by=?, reviewedat=? WHERE id=?",
            (decision, reviewed_by, now, cid)
        )
    return {"id": cid, "decision": decision, "reviewed_by": reviewed_by}
