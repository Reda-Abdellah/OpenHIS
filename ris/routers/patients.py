import os
import httpx
from fastapi import APIRouter, HTTPException
from database import get_db, rows_to_list, row_to_dict

router      = APIRouter(prefix="/api/patients", tags=["patients"])
ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")


@router.get("")
def list_patients():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM patients ORDER BY patient_name"
        ).fetchall()
    return rows_to_list(rows)


@router.post("/sync")
async def sync_patients():
    """Pull all patients from Orthanc and upsert into local DB."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            ids_resp = await client.get(f"{ORTHANC_URL}/patients")
            if ids_resp.status_code != 200:
                raise HTTPException(502, "Orthanc /patients unreachable")
            orthanc_ids: list[str] = ids_resp.json()

            details = []
            for oid in orthanc_ids:
                r = await client.get(f"{ORTHANC_URL}/patients/{oid}")
                if r.status_code == 200:
                    details.append((oid, r.json()))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Orthanc error: {e}")

    added = updated = 0
    with get_db() as db:
        for oid, data in details:
            tags  = data.get("MainDicomTags", {})
            pid   = tags.get("PatientID",        "")
            pname = tags.get("PatientName",       "UNKNOWN")
            dob   = tags.get("PatientBirthDate",  "")
            sex   = tags.get("PatientSex",        "")

            # format DOB 19850615 → 1985-06-15
            if len(dob) == 8:
                dob = f"{dob[:4]}-{dob[4:6]}-{dob[6:]}"

            existing = db.execute(
                "SELECT id FROM patients WHERE orthanc_id=?", (oid,)
            ).fetchone()

            if existing:
                db.execute(
                    """UPDATE patients SET patient_id=?,patient_name=?,
                       birth_date=?,sex=? WHERE orthanc_id=?""",
                    (pid, pname, dob, sex, oid),
                )
                updated += 1
            else:
                db.execute(
                    """INSERT INTO patients
                       (orthanc_id, patient_id, patient_name, birth_date, sex)
                       VALUES (?,?,?,?,?)""",
                    (oid, pid, pname, dob, sex),
                )
                added += 1

    return {"added": added, "updated": updated, "total": len(details)}
