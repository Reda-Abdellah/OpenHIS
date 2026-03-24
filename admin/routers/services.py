import asyncio, datetime, os, time
import httpx
from fastapi import APIRouter, Depends
from security import require_admin

router = APIRouter(prefix="/api/services", tags=["services"])

SERVICES = [
    {"name": "EHR",            "url": os.environ.get('EHR_HEALTH',  'http://ehr:8003/api/health'),            "path": "/ehr"},
    {"name": "RIS",            "url": os.environ.get('RIS_HEALTH',  'http://ris:8002/api/health'),            "path": "/ris"},
    {"name": "AI Controller",  "url": os.environ.get('AI_HEALTH',   'http://ai-controller:8000/api/health'),  "path": "/ai"},
    {"name": "FHIR Bridge",    "url": os.environ.get('FHIR_HEALTH', 'http://fhir-bridge:8005/api/health'),    "path": "/fhir-bridge"},
    {"name": "MPI",            "url": os.environ.get('MPI_HEALTH',  'http://mpi:8007/api/health'),            "path": "/mpi"},
    {"name": "HL7 Gateway",    "url": os.environ.get('HL7_HEALTH',  'http://hl7:8009/api/health'),            "path": "/hl7"},
    {"name": "Patient Portal", "url": os.environ.get('PP_HEALTH',   'http://patient-portal:8010/api/health'), "path": "/patient-portal"},
    {"name": "Analytics",      "url": os.environ.get('AN_HEALTH',   'http://analytics:8008/api/health'),      "path": "/analytics"},
    {"name": "Orthanc PACS",   "url": os.environ.get('PACS_HEALTH', 'http://orthanc:8042/system'),            "path": None},
]


async def _check_service(name: str, url: str, path: str | None) -> dict:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r   = await c.get(url)
            ms  = round((time.monotonic() - t0) * 1000)
            data = {}
            try:
                data = r.json()
            except Exception:
                pass
            status = "online" if r.status_code < 400 else "degraded"
            return {"name": name, "url": url, "path": path,
                    "status": status, "http_status": r.status_code,
                    "response_ms": ms, "data": data}
    except Exception as e:
        ms = round((time.monotonic() - t0) * 1000)
        return {"name": name, "url": url, "path": path,
                "status": "offline", "response_ms": ms,
                "error": str(e)[:80]}


@router.get("")
async def get_services(session: dict = Depends(require_admin)):
    tasks   = [_check_service(s["name"], s["url"], s.get("path"))
               for s in SERVICES]
    results = await asyncio.gather(*tasks)
    online  = sum(1 for r in results if r["status"] == "online")
    offline = sum(1 for r in results if r["status"] == "offline")
    degraded= sum(1 for r in results if r["status"] == "degraded")
    return {
        "services":   results,
        "online":     online,
        "offline":    offline,
        "degraded":   degraded,
        "total":      len(results),
        "checked_at": datetime.datetime.utcnow().isoformat(timespec='seconds'),
    }
