"""
OpenMRS → RIS sync worker.

Polls OpenMRS FHIR for imaging ServiceRequest resources and auto-registers
the referenced patients + creates RIS orders so the RIS stays in sync without
manual data entry.

Category filter: SNOMED 363679005 (Imaging)

Env vars:
  OPENMRS_URL           http://openmrs:8080
  KEYCLOAK_TOKEN_URL    (required)
  KEYCLOAK_CLIENT_ID    (required)
  KEYCLOAK_CLIENT_SECRET (required)
  POLL_INTERVAL_S       60
  DB_PATH               /data/ris.db
"""
import asyncio
import logging
import os

import httpx

from database import get_db

log = logging.getLogger("ris.omrs_sync")

OPENMRS_URL     = os.environ.get("OPENMRS_URL", "http://openmrs:8080")
POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "60"))

FHIR_BASE = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"

# In-memory dedup — reset on restart (all upserts are idempotent anyway)
_seen_sr_ids: set[str] = set()


# ── helpers ───────────────────────────────────────────────────────────────────

async def _fhir_get(client: httpx.AsyncClient, path: str,
                    params: dict | None = None) -> dict:
    r = await client.get(
        f"{FHIR_BASE}/{path}",
        params=params or {},
        headers={"Accept": "application/fhir+json"},
    )
    r.raise_for_status()
    return r.json()


def _upsert_patient_local(mrn: str, patient_name: str,
                          birth_date: str, sex: str) -> int:
    """Insert-or-update RIS patient row; return local integer id."""
    with get_db() as db:
        row = db.execute("SELECT id FROM patients WHERE mrn=?", (mrn,)).fetchone()
        if row:
            return row["id"]
        cur = db.execute(
            "INSERT INTO patients (mrn, patient_name, birth_date, sex)"
            " VALUES (?,?,?,?)",
            (mrn, patient_name, birth_date, sex),
        )
        return cur.lastrowid


def _order_exists(accession: str) -> bool:
    with get_db() as db:
        return db.execute(
            "SELECT id FROM orders WHERE accession_number=?", (accession,)
        ).fetchone() is not None


def _create_order_local(patient_id: int, modality: str, body_part: str,
                        accession: str, clinical_info: str) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO orders"
            " (patient_id, modality, body_part, accession_number, clinical_info, status)"
            " VALUES (?,?,?,?,?,'PENDING')",
            (patient_id, modality, body_part, accession, clinical_info),
        )
        return cur.lastrowid


# ── sync logic ────────────────────────────────────────────────────────────────

async def _sync_once(client: httpx.AsyncClient) -> int:
    """Fetch recent imaging ServiceRequests from OpenMRS and mirror into RIS."""
    try:
        bundle = await _fhir_get(client, "ServiceRequest", {
            "category": "363679005",   # SNOMED: Imaging
            "_sort": "-_lastUpdated",
            "_count": "50",
        })
    except Exception as exc:
        log.warning("OpenMRS FHIR query failed: %s", exc)
        return 0

    created = 0
    for entry in bundle.get("entry", []):
        sr = entry.get("resource", {})
        sr_id = sr.get("id", "")
        if not sr_id or sr_id in _seen_sr_ids:
            continue

        # Derive accession number from identifier, fall back to FHIR id
        accession = sr_id
        for ident in sr.get("identifier", []):
            if ident.get("value"):
                accession = ident["value"]
                break

        if _order_exists(accession):
            _seen_sr_ids.add(sr_id)
            continue

        # Resolve patient from subject reference
        subject_ref = sr.get("subject", {}).get("reference", "")
        pt_fhir_id = subject_ref.split("/")[-1] if subject_ref else ""
        if not pt_fhir_id:
            log.debug("ServiceRequest %s has no subject — skipping", sr_id)
            continue

        try:
            pt = await _fhir_get(client, f"Patient/{pt_fhir_id}")
        except Exception as exc:
            log.warning("Could not fetch Patient/%s: %s", pt_fhir_id, exc)
            continue

        # Extract demographics
        mrn = pt_fhir_id  # fallback to FHIR UUID
        for ident in pt.get("identifier", []):
            if ident.get("value"):
                mrn = ident["value"]
                break

        names = pt.get("name", [{}])
        given  = " ".join(names[0].get("given", [])) if names else ""
        family = names[0].get("family", "") if names else ""
        patient_name = f"{given} {family}".strip() or mrn
        birth_date   = pt.get("birthDate", "")
        sex_map = {"male": "M", "female": "F", "other": "O", "unknown": "U"}
        sex = sex_map.get(pt.get("gender", "unknown"), "U")

        local_patient_id = _upsert_patient_local(mrn, patient_name, birth_date, sex)

        # Extract imaging order details
        code_obj = sr.get("code", {})
        codings  = code_obj.get("coding", [])
        modality = code_obj.get("text", "") or (
            codings[0].get("display", codings[0].get("code", "UNKNOWN"))
            if codings else "UNKNOWN"
        )
        body_parts = sr.get("bodySite", [])
        body_part  = ""
        if body_parts:
            bp_codings = body_parts[0].get("coding", [])
            body_part  = body_parts[0].get("text", "") or (
                bp_codings[0].get("display", "") if bp_codings else ""
            )
        notes        = sr.get("note", [])
        clinical_info = notes[0].get("text", "") if notes else ""

        ris_order_id = _create_order_local(
            local_patient_id, modality, body_part, accession, clinical_info,
        )
        _seen_sr_ids.add(sr_id)
        created += 1
        log.info(
            "Created RIS order id=%d from OpenMRS SR/%s (MRN=%s modality=%s)",
            ris_order_id, sr_id, mrn, modality,
        )

    return created


async def sync_loop() -> None:
    """Background task — runs forever, sleeping POLL_INTERVAL_S between cycles."""
    from token import get_service_token
    log.info(
        "OpenMRS→RIS sync started (interval=%ds fhir=%s)",
        POLL_INTERVAL_S, FHIR_BASE,
    )
    while True:
        try:
            token = await get_service_token()
            hdrs = {
                "Accept": "application/fhir+json",
                "Authorization": f"Bearer {token}",
            }
            async with httpx.AsyncClient(headers=hdrs, timeout=15) as client:
                n = await _sync_once(client)
                if n:
                    log.info("Mirrored %d new imaging orders from OpenMRS", n)
        except Exception as exc:
            log.error("OpenMRS sync loop error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_S)
