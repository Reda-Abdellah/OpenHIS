"""
Pulls metrics from OpenMRS, OpenELIS, RIS, and AI Controller,
then stores JSON snapshots in SQLite.

Domain keys (preserved for frontend compat):
  ehr     → patient / encounter counts from OpenMRS
  orders  → lab/imaging order counts from OpenMRS + OpenELIS + RIS
  lis     → lab result counts from OpenELIS FHIR
  ai      → AI pipeline job metrics (unchanged)
"""
import datetime
import json
import logging
import os

import httpx

from database import get_db

log = logging.getLogger("analytics.collector")

OPENMRS_URL  = os.environ.get("OPENMRS_URL",  "http://openmrs:8080")
OPENELIS_URL = os.environ.get("OPENELIS_URL", "http://openelis:8080")
RIS_URL      = os.environ.get("RIS_URL",       "http://ris:8002/api")
AI_URL       = os.environ.get("AI_CONTROLLER_URL", "http://ai-controller:8000/api")

_OMRS_FHIR = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
_OE_FHIR   = f"{OPENELIS_URL}/fhir/R4"

_LAST_REFRESH: dict = {}


async def _get(client: httpx.AsyncClient, url: str, headers: dict = None, params: dict = None):
    try:
        r = await client.get(url, headers=headers, params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"GET {url} → {type(e).__name__}: {e}")
        return None


async def _fhir_count(client: httpx.AsyncClient, url: str, headers: dict,
                      params: dict = None) -> int:
    p = dict(params or {})
    p.update({"_count": "0", "_summary": "count"})
    data = await _get(client, url, headers=headers, params=p)
    return (data or {}).get("total", 0)


async def collect_all() -> dict:
    from sa_token import get_service_token
    result = {}
    token  = await get_service_token()
    auth_hdr = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=12.0) as c:

        # ── EHR domain → OpenMRS ──────────────────────────────────────────────
        total_patients = await _fhir_count(c, f"{_OMRS_FHIR}/Patient",   auth_hdr)
        active_enc     = await _fhir_count(c, f"{_OMRS_FHIR}/Encounter", auth_hdr,
                                           {"status": "in-progress"})
        today_str      = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        new_today      = await _fhir_count(c, f"{_OMRS_FHIR}/Patient",   auth_hdr,
                                           {"_lastUpdated": f"ge{today_str}"})
        result["ehr"] = {
            "total_patients":     total_patients,
            "active_encounters":  active_enc,
            "new_patients_today": new_today,
            "ward_breakdown":     {},
        }

        # ── Orders domain ─────────────────────────────────────────────────────
        lab_active    = await _fhir_count(c, f"{_OMRS_FHIR}/ServiceRequest", auth_hdr,
                                          {"status": "active"})
        lab_final     = await _fhir_count(c, f"{_OE_FHIR}/DiagnosticReport", auth_hdr,
                                          {"status": "final"})
        ris_orders    = await _get(c, f"{RIS_URL}/orders")
        img_completed = sum(1 for o in (ris_orders or []) if o.get("status") == "COMPLETED")
        img_pending   = sum(1 for o in (ris_orders or []) if o.get("status") != "COMPLETED")
        result["orders"] = {
            "lab_pending":       lab_active,
            "lab_completed":     lab_final,
            "lab_tat_hours":     None,
            "imaging_pending":   img_pending,
            "imaging_completed": img_completed,
            "imaging_tat_hours": None,
        }

        # ── LIS domain → OpenELIS ─────────────────────────────────────────────
        oe_final       = await _fhir_count(c, f"{_OE_FHIR}/DiagnosticReport", auth_hdr,
                                           {"status": "final"})
        oe_preliminary = await _fhir_count(c, f"{_OE_FHIR}/DiagnosticReport", auth_hdr,
                                           {"status": "preliminary"})
        result["lis"] = {
            "final_reports":   oe_final,
            "pending_reports": oe_preliminary,
        }

        # ── AI domain (unchanged) ─────────────────────────────────────────────
        jobs = await _get(c, f"{AI_URL}/jobs?limit=500")
        if jobs is not None:
            by_status: dict = {}
            for j in jobs:
                k = j.get("status", "UNKNOWN")
                by_status[k] = by_status.get(k, 0) + 1
            durations = [j["durationms"] for j in jobs if j.get("durationms")]
            total_j   = len(jobs)
            completed = by_status.get("COMPLETED", 0)
            result["ai"] = {
                "total":           total_j,
                "by_status":       by_status,
                "success_rate":    round(completed / total_j * 100, 1) if total_j else 0,
                "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
                "failed":          by_status.get("FAILED", 0),
                "running":         by_status.get("RUNNING", 0),
            }

    return result


async def collect_and_store():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        data = await collect_all()
        with get_db() as db:
            for domain, payload in data.items():
                if payload is not None:
                    db.execute(
                        "INSERT INTO snapshots(domain,data,captured_at) VALUES(?,?,?)",
                        (domain, json.dumps(payload), now)
                    )
                    _LAST_REFRESH[domain] = now
            db.execute("DELETE FROM snapshots WHERE captured_at < datetime('now', '-90 days')")
        log.info(f"Metrics stored: domains={list(data.keys())} at {now}")
    except Exception as e:
        log.error(f"collect_and_store failed: {e}")
