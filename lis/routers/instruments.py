"""
Simulated laboratory instrument interface.
Mimics hematology, chemistry, and immunology analysers.
Follows the same pattern as the existing DICOM simulator.
"""
import random, datetime
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/instruments", tags=["instruments"])

INSTRUMENTS = {
    "HEMA-01":  {"type": "hematology",   "tests": ["CBC"]},
    "CHEM-01":  {"type": "chemistry",    "tests": ["BMP", "CMP", "LFT", "LIPID", "HBA1C", "IRON", "UA", "CRP"]},
    "IMMUNO-01":{"type": "immunology",   "tests": ["TSH", "THYROID", "TROPONIN", "CARDIAC", "VITAMIN_B12"]},
    "MICRO-01": {"type": "microbiology", "tests": ["BLOOD_CULTURE", "URINE_CULTURE"]},
    "COAG-01":  {"type": "coagulation",  "tests": ["COAG"]},
}

NORMAL_RANGES = {
    "WBC":       (4.0, 11.0, "10^9/L"),
    "RBC":       (4.2, 5.4, "10^12/L"),
    "Hemoglobin":(12.0, 17.5, "g/dL"),
    "Hematocrit":(36.0, 52.0, "%"),
    "Platelets": (150.0, 400.0, "10^9/L"),
    "Na":        (136.0, 146.0, "mmol/L"),
    "K":         (3.5, 5.1, "mmol/L"),
    "Cl":        (98.0, 107.0, "mmol/L"),
    "CO2":       (22.0, 29.0, "mmol/L"),
    "BUN":       (2.5, 7.1, "mmol/L"),
    "Creatinine":(53.0, 115.0, "µmol/L"),
    "Glucose":   (3.9, 6.1, "mmol/L"),
    "AST":       (10.0, 40.0, "U/L"),
    "ALT":       (7.0, 56.0, "U/L"),
    "ALP":       (44.0, 147.0, "U/L"),
    "Bilirubin": (3.0, 21.0, "µmol/L"),
    "Cholesterol":(0.0, 5.2, "mmol/L"),
    "LDL":       (0.0, 3.4, "mmol/L"),
    "HDL":       (1.0, 3.0, "mmol/L"),
    "Triglycerides":(0.0, 1.7, "mmol/L"),
    "TSH":       (0.4, 4.0, "mIU/L"),
    "Troponin":  (0.0, 0.04, "µg/L"),
    "HbA1c":     (4.0, 5.7, "%"),
    # CBC differential
    "Neutrophils":   (1.8, 7.7, "%"),
    "Lymphocytes":   (1.0, 4.8, "%"),
    "Monocytes":     (0.2, 1.0, "%"),
    "Eosinophils":   (0.0, 0.5, "%"),
    "Basophils":     (0.0, 0.1, "%"),
    "MCV":           (80.0, 100.0, "fL"),
    "MCH":           (27.0, 33.0, "pg"),
    "MCHC":          (32.0, 36.0, "g/dL"),
    # BMP/CMP extras
    "Calcium":       (2.2, 2.6, "mmol/L"),
    "Magnesium":     (0.7, 1.1, "mmol/L"),
    "Phosphorus":    (0.8, 1.5, "mmol/L"),
    "Albumin":       (35.0, 50.0, "g/L"),
    "TotalProtein":  (60.0, 80.0, "g/L"),
    "GGT":           (9.0, 48.0, "U/L"),
    "LDH":           (135.0, 225.0, "U/L"),
    "Uric_Acid":     (0.18, 0.42, "mmol/L"),
    # Coagulation
    "PT":            (11.0, 13.5, "sec"),
    "PTT":           (25.0, 35.0, "sec"),
    "INR":           (0.8, 1.1, "ratio"),
    "Fibrinogen":    (2.0, 4.0, "g/L"),
    "D_Dimer":       (0.0, 0.5, "mg/L FEU"),
    # Immunology extras
    "Free_T4":       (12.0, 22.0, "pmol/L"),
    "Free_T3":       (3.1, 6.8, "pmol/L"),
    "CRP":           (0.0, 5.0, "mg/L"),
    "Procalcitonin": (0.0, 0.5, "ng/mL"),
    "Ferritin":      (12.0, 300.0, "ng/mL"),
    "Vitamin_B12":   (200.0, 900.0, "pg/mL"),
    "Folate":        (3.1, 17.5, "ng/mL"),
    "Iron":          (10.7, 32.2, "µmol/L"),
    "TIBC":          (45.0, 73.0, "µmol/L"),
    # Cardiac
    "CK":            (30.0, 200.0, "U/L"),
    "CK_MB":         (0.0, 25.0, "U/L"),
    "BNP":           (0.0, 100.0, "pg/mL"),
    "Myoglobin":     (0.0, 85.0, "ng/mL"),
    # Urine
    "Urine_Protein": (0.0, 0.15, "g/L"),
    "Urine_Glucose": (0.0, 0.8, "mmol/L"),
    "Urine_Creatinine": (5.3, 15.9, "mmol/L"),
    "Urine_WBC":     (0.0, 5.0, "cells/hpf"),
    "Urine_RBC":     (0.0, 3.0, "cells/hpf"),
    "Urine_pH":      (4.5, 8.0, "pH"),
    "Urine_Specific_Gravity": (1.005, 1.030, "ratio"),
}

CBC_ANALYTES   = ["WBC", "RBC", "Hemoglobin", "Hematocrit", "Platelets",
                   "Neutrophils", "Lymphocytes", "Monocytes", "Eosinophils", "Basophils",
                   "MCV", "MCH", "MCHC"]
BMP_ANALYTES   = ["Na", "K", "Cl", "CO2", "BUN", "Creatinine", "Glucose",
                   "Calcium", "Magnesium"]
LFT_ANALYTES   = ["AST", "ALT", "ALP", "Bilirubin", "Albumin", "TotalProtein", "GGT", "LDH"]
LIPID_ANALYTES = ["Cholesterol", "LDL", "HDL", "Triglycerides"]
COAG_ANALYTES  = ["PT", "PTT", "INR", "Fibrinogen", "D_Dimer"]
UA_ANALYTES    = ["Urine_Protein", "Urine_Glucose", "Urine_Creatinine",
                  "Urine_WBC", "Urine_RBC", "Urine_pH", "Urine_Specific_Gravity"]
CARDIAC_ANALYTES = ["Troponin", "CK", "CK_MB", "BNP", "Myoglobin"]
THYROID_ANALYTES = ["TSH", "Free_T4", "Free_T3"]
IRON_ANALYTES    = ["Iron", "TIBC", "Ferritin"]
INFLAM_ANALYTES  = ["CRP", "Procalcitonin"]

TEST_ANALYTES = {
    # Hematology
    "CBC":           CBC_ANALYTES,
    # Chemistry
    "BMP":           BMP_ANALYTES,
    "CMP":           BMP_ANALYTES + LFT_ANALYTES + ["Phosphorus", "Uric_Acid"],
    "LFT":           LFT_ANALYTES,
    "LIPID":         LIPID_ANALYTES,
    "HBA1C":         ["HbA1c"],
    # Coagulation
    "COAG":          COAG_ANALYTES,
    # Immunology / Endocrinology
    "TSH":           ["TSH"],
    "THYROID":       THYROID_ANALYTES,
    "TROPONIN":      ["Troponin"],
    "CARDIAC":       CARDIAC_ANALYTES,
    "IRON":          IRON_ANALYTES,
    "CRP":           INFLAM_ANALYTES,
    "VITAMIN_B12":   ["Vitamin_B12", "Folate"],
    # Urine
    "UA":            UA_ANALYTES,
    "URINE_CULTURE": UA_ANALYTES,  # simplified — real culture would be organism-based
    # Microbiology (flag-only simulation)
    "BLOOD_CULTURE": [],
}

def _simulate_value(analyte: str, abnormal_chance: float = 0.15) -> tuple[str, str, str]:
    if analyte not in NORMAL_RANGES:
        return "N/A", "", ""
    lo, hi, unit = NORMAL_RANGES[analyte]
    rng = hi - lo
    if random.random() < abnormal_chance:
        # generate high or low outlier
        if random.random() < 0.5:
            val = lo - random.uniform(0.1, 0.3) * rng
            flag = "L" if val > lo - 0.5 * rng else "LL"
        else:
            val = hi + random.uniform(0.1, 0.3) * rng
            flag = "H" if val < hi + 0.5 * rng else "HH"
    else:
        val = lo + random.random() * rng
        flag = "normal"
    return f"{val:.2f}", unit, flag

class RunRequest(BaseModel):
    instrument_id: str
    order_ids: list[int]

@router.get("")
def list_instruments():
    return [{"instrument_id": k, **v} for k, v in INSTRUMENTS.items()]

@router.get("/runs")
def list_runs(instrument_id: Optional[str] = None):
    with get_db() as db:
        if instrument_id:
            rows = db.execute(
                "SELECT * FROM instrument_runs WHERE instrument_id=? ORDER BY run_started DESC LIMIT 50",
                (instrument_id,)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM instrument_runs ORDER BY run_started DESC LIMIT 50").fetchall()
        return rows_to_list(rows)

@router.post("/run", status_code=202)
async def run_instrument(body: RunRequest, bg: BackgroundTasks):
    if body.instrument_id not in INSTRUMENTS:
        from fastapi import HTTPException
        raise HTTPException(404, f"Unknown instrument {body.instrument_id}")
    bg.add_task(_run_instrument_task, body.instrument_id, body.order_ids)
    return {"status": "queued", "instrument_id": body.instrument_id, "order_count": len(body.order_ids)}

async def _run_instrument_task(instrument_id: str, order_ids: list[int]):
    import asyncio
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO instrument_runs(instrument_id,instrument_type,run_started,orders_processed) VALUES(?,?,?,?)",
            (instrument_id, INSTRUMENTS[instrument_id]["type"], now, len(order_ids)))
        run_id = cur.lastrowid

    await asyncio.sleep(2)  # Simulate processing time

    processed = 0
    for oid in order_ids:
        with get_db() as db:
            row = db.execute("SELECT test_code FROM lab_orders WHERE id=?", (oid,)).fetchone()
            if not row:
                continue
            inst_tests = INSTRUMENTS[instrument_id]["tests"]
            if row["test_code"] not in inst_tests:
                continue
            analytes = TEST_ANALYTES.get(row["test_code"], [])
            for analyte in analytes:
                val, unit, flag = _simulate_value(analyte)
                rng_entry = NORMAL_RANGES.get(analyte)
                ref_range = f"{rng_entry[0]}-{rng_entry[1]}" if rng_entry else None
                db.execute(
                    "INSERT INTO lab_results(order_id,analyte,value,unit,reference_range,flag,instrument_id,status) VALUES(?,?,?,?,?,?,?,?)",
                    (oid, analyte, val, unit, ref_range, flag, instrument_id, "preliminary"))
            db.execute(
                "UPDATE lab_orders SET status='IN_PROGRESS', instrument_id=? WHERE id=?",
                (instrument_id, oid))
        processed += 1

    finish = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "UPDATE instrument_runs SET status='completed', run_finished=?, orders_processed=? WHERE id=?",
            (finish, processed, run_id))
