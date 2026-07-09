import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional
from database import get_db, row_to_dict, rows_to_list
from parser  import parse as hl7_parse
from builder import build_ack

router = APIRouter(prefix="/api/messages", tags=["messages"])


def _log(raw: str, direction: str, parsed: dict) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO messages"
            "(direction,msg_type,control_id,sending_app,patient_id,patient_name,raw) "
            "VALUES(?,?,?,?,?,?,?)",
            (direction,
             parsed.get("msg_type", "UNKNOWN"),
             parsed.get("control_id"),
             parsed.get("sending_app"),
             parsed.get("mrn"),
             parsed.get("patient_name"),
             raw)
        )
        return cur.lastrowid


@router.get("")
def list_messages(
    direction:  Optional[str] = None,
    msg_type:   Optional[str] = None,
    status:     Optional[str] = None,
    limit:      int = 200,
):
    clauses, params = [], []
    if direction: clauses.append("direction=?");  params.append(direction)
    if msg_type:  clauses.append("msg_type=?");   params.append(msg_type)
    if status:    clauses.append("status=?");      params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT id,direction,msg_type,control_id,sending_app,"
            f"patient_id,patient_name,status,error_msg,created_at "
            f"FROM messages {where} ORDER BY created_at DESC LIMIT ?",
            params
        ).fetchall())


@router.get("/stats")
def get_stats():
    with get_db() as db:
        total    = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        inbound  = db.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='inbound'").fetchone()[0]
        outbound = db.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='outbound'").fetchone()[0]
        errors   = db.execute(
            "SELECT COUNT(*) FROM messages WHERE status='error'").fetchone()[0]
        by_type  = rows_to_list(db.execute(
            "SELECT msg_type, COUNT(*) AS cnt FROM messages "
            "GROUP BY msg_type ORDER BY cnt DESC"
        ).fetchall())
        today    = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
        today_ct = db.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ?", (today,)
        ).fetchone()[0]
    return {
        "total": total, "inbound": inbound, "outbound": outbound,
        "errors": errors, "today": today_ct, "by_type": by_type,
    }


@router.get("/{mid}")
def get_message(mid: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not row:
        raise HTTPException(404, "Message not found")
    return row_to_dict(row)


@router.post("/inbound", status_code=202)
async def receive_inbound(body: dict, bg: BackgroundTasks):
    """Accept a raw HL7 message via REST (non-MLLP path)."""
    raw = body.get("raw") or body.get("message", "")
    if not raw:
        raise HTTPException(400, "'raw' field required")
    try:
        parsed = hl7_parse(raw)
    except Exception as e:
        raise HTTPException(422, f"HL7 parse error: {e}")

    msg_id = _log(raw, "inbound", parsed)
    ack    = build_ack(parsed.get("control_id", ""), "AA",
                       f"{parsed.get('msg_type','?')} accepted")

    from handlers import dispatch_and_update
    bg.add_task(dispatch_and_update, raw, msg_id)

    return {
        "status":   "accepted",
        "msg_type": parsed.get("msg_type"),
        "msg_id":   msg_id,
        "ack":      ack,
    }
