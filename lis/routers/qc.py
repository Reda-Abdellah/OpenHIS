from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/qc", tags=["qc"])

class QCEntry(BaseModel):
    instrument_id: str
    test_code: str
    lot_number: str
    qc_level: str        # low / normal / high
    result_value: float
    expected_mean: float
    expected_sd: float

def _westgard(value: float, mean: float, sd: float) -> tuple[str, int]:
    """Evaluate basic Westgard multi-rules. Returns (flag, pass_int)."""
    z = (value - mean) / sd if sd else 0
    if abs(z) > 3:   return "1-3s", 0    # 1:3s warning — reject
    if abs(z) > 2:   return "1-2s", 1    # 1:2s warning — warn only
    return "pass", 1

@router.get("")
def list_qc(instrument_id: Optional[str] = None, test_code: Optional[str] = None):
    clauses, params = [], []
    if instrument_id: clauses.append("instrument_id=?"); params.append(instrument_id)
    if test_code:     clauses.append("test_code=?");     params.append(test_code)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM qc_records {where} ORDER BY recorded_at DESC LIMIT 200", params).fetchall())

@router.post("", status_code=201)
def record_qc(body: QCEntry):
    flag, passed = _westgard(body.result_value, body.expected_mean, body.expected_sd)
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO qc_records(instrument_id,test_code,lot_number,qc_level,result_value,expected_mean,expected_sd,westgard_flag,pass) VALUES(?,?,?,?,?,?,?,?,?)",
            (body.instrument_id, body.test_code, body.lot_number, body.qc_level,
             body.result_value, body.expected_mean, body.expected_sd, flag, passed))
        row = db.execute("SELECT * FROM qc_records WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)
