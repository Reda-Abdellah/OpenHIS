# OpenHIS — Detailed Mitigation Plan by Phase


***

## Phase 1 — Security Hardening

### 1.1 Strip Hardcoded Credentials

**Where:** `services/hl7/app/handlers.py`, all `config.py` files, `.env.example`

**Step-by-step:**

1. Grep all services for `os.getenv(…, "…")` patterns where the second argument is a non-empty string credential (password, secret, token).
2. Replace every such default with `None`:
```python
# BEFORE
OPENMRS_PASS = os.getenv("OPENMRS_PASS", "Admin123")

# AFTER
OPENMRS_PASS = os.getenv("OPENMRS_PASS")
```

3. Add a startup validation block at the top of each service's `main.py`, before the FastAPI app is created:
```python
import sys

_REQUIRED_ENV = ["OPENMRS_USER", "OPENMRS_PASS", "FHIR_BASE_URL"]

def _check_env():
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")

_check_env()
```

4. Update `.env.example`: replace all `changeme`, `admin`, `Admin123` values with `CHANGE_ME_BEFORE_DEPLOY` and add a comment block at the top:
```bash
# ⚠️  SECURITY: All values marked CHANGE_ME_BEFORE_DEPLOY MUST be set
# before running this stack in any environment. Never commit real values.
```

5. Add a `.gitignore` rule to block accidental `.env` commits (verify it already exists — if not, add `*.env` and `.env`).

**Test:** Add a unit test that mocks `os.getenv` to return `None` for a required var and asserts `SystemExit` is raised.

***

### 1.2 JWT Auth Middleware on All FastAPI Services

**Where:** `services/mpi/app/main.py`, `services/integration-hub/app/main.py`, and all other service `main.py` files.

**Step-by-step:**

1. Create a shared auth module. Since services don't share a library today, copy it to each service under `app/auth.py`:
```python
import os
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

_bearer = HTTPBearer()
_JWKS_URI = os.getenv("KEYCLOAK_JWKS_URI")  # e.g. http://keycloak:8080/realms/openhis/protocol/openid-connect/certs
_AUDIENCE = os.getenv("KEYCLOAK_AUDIENCE", "openhis-services")

async def require_token(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    token = creds.credentials
    try:
        # Fetch JWKS on first call; cache in a module-level variable
        payload = jwt.decode(token, _get_jwks(), algorithms=["RS256"], audience=_AUDIENCE)
        return payload
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
```

2. Apply the dependency to every router **except** `/health`:
```python
# In main.py
from app.auth import require_token

app.include_router(patients_router, dependencies=[Depends(require_token)])
app.include_router(crossref_router, dependencies=[Depends(require_token)])
# Health stays open:
app.include_router(health_router)
```

3. Add `KEYCLOAK_JWKS_URI` and `KEYCLOAK_AUDIENCE` to `.env.example` and each service's required env check from step 1.1.
4. Add JWKS caching with a 5-minute TTL to avoid hammering Keycloak on every request:
```python
import time
_jwks_cache = {"keys": None, "fetched_at": 0}

def _get_jwks():
    if time.time() - _jwks_cache["fetched_at"] > 300:
        resp = httpx.get(_JWKS_URI, timeout=5)
        _jwks_cache["keys"] = resp.json()
        _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"]
```

**Test:** Write a pytest fixture that generates a signed JWT with a test RSA keypair, mocks `_get_jwks()` to return the public key, and asserts that valid tokens pass and expired/tampered tokens return 401.

***

### 1.3 MLLP Message Size Limit

**Where:** `services/hl7/` — the MLLP TCP listener

**Step-by-step:**

1. Locate the asyncio TCP read loop in the HL7 service.
2. Add a hard cap on bytes read before passing to the HL7 parser:
```python
MAX_MSG_BYTES = int(os.getenv("MLLP_MAX_MSG_BYTES", 1_048_576))  # 1 MB default

async def handle_mllp_connection(reader, writer):
    data = await reader.read(MAX_MSG_BYTES + 1)
    if len(data) > MAX_MSG_BYTES:
        logger.warning("MLLP message exceeded max size, dropping connection")
        writer.close()
        return
    # ... parse and process
```

3. Add `MLLP_MAX_MSG_BYTES` to `.env.example` with a comment explaining the tradeoff for large ORU^R01 lab messages.
4. Add a connection-level timeout so a slow sender cannot hold a socket open indefinitely:
```python
try:
    data = await asyncio.wait_for(reader.read(MAX_MSG_BYTES + 1), timeout=30.0)
except asyncio.TimeoutError:
    logger.warning("MLLP connection timed out")
    writer.close()
    return
```


***

## Phase 2 — Integration Bus Completion

### 2.1 Missing Bus Consumers (Analytics, HL7, AI-Controller)

**Where:** New files in `services/analytics/app/bus_consumer.py`, `services/hl7/app/bus_consumer.py`, `services/ai-controller/app/bus_consumer.py`

**Step-by-step:**

**A. Create a shared consumer base pattern.** All three follow the same structure — copy from MPI's `bus_consumer.py` and parameterise:

```python
# services/analytics/app/bus_consumer.py
import asyncio, json, logging, os
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
STREAM = "openhis:events"
GROUP = "analytics"
CONSUMER = "analytics-1"

async def consume_loop():
    r = aioredis.from_url(REDIS_URL)
    # Ensure group exists (idempotent)
    try:
        await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    while True:
        try:
            messages = await r.xreadgroup(
                GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000
            )
            for _, msgs in (messages or []):
                for msg_id, fields in msgs:
                    event_type = fields.get(b"type", b"").decode()
                    payload = json.loads(fields.get(b"payload", b"{}"))
                    await _dispatch(event_type, payload)
                    await r.xack(STREAM, GROUP, msg_id)
        except Exception as e:
            logger.error(f"Bus consumer error: {e}")
            await asyncio.sleep(5)

async def _dispatch(event_type: str, payload: dict):
    if event_type == "patient.synced":
        await _record_patient_event(payload)
    elif event_type == "lab_result.ready":
        await _record_lab_result(payload)
    elif event_type == "lab_order.routed":
        await _record_lab_order(payload)
    # ... extend as needed
```

**B. Wire each consumer into its service's `lifespan` (replacing the deprecated `@app.on_event`):**

```python
# In each service's main.py
from contextlib import asynccontextmanager
from app.bus_consumer import consume_loop
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(consume_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
```

**C. Events each service must handle:**


| Service | Event types | Action |
| :-- | :-- | :-- |
| `analytics` | `patient.synced`, `lab_order.routed`, `lab_result.ready`, `dicom.stored` | Write to analytics DB / aggregate counters |
| `hl7` | `lab_result.ready` | Build and send outbound ORU^R01 message to downstream systems |
| `ai-controller` | `dicom.stored` | Trigger AI inference job for the referenced study UID |


***

### 2.2 Publish Bus Events from `events.py`

**Where:** `services/integration-hub/app/routers/events.py`

**Step-by-step:**

1. Import `bus` at the top of `events.py`:
```python
from app import bus
```

2. After each successful FHIR POST, add a `bus.publish()` call. Example for Orthanc DICOM webhook:
```python
@router.post("/orthanc/stored")
async def orthanc_stored(payload: dict):
    # Existing FHIR translation + POST to OpenMRS
    await _post_fhir_imaging_study(payload)
    
    # NEW: publish to bus
    await bus.publish("dicom.stored", {
        "study_uid": payload.get("StudyInstanceUID"),
        "patient_id": payload.get("PatientID"),
        "modality": payload.get("Modality"),
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "ok"}
```

3. Similarly for RIS reports (`radiology.report.ready`) and AI results (`ai.result.ready`).
4. Verify `bus.publish()` already exists and works — it does, via `XADD` on the Redis stream. No changes needed to `bus.py` itself.

***

### 2.3 Unified Retry + Audit in `events.py`

**Where:** New `services/integration-hub/app/utils/retry.py` + `events.py`

**Step-by-step:**

1. Extract the retry decorator from `worker.py` into `app/utils/retry.py`:
```python
import asyncio, functools, logging

def with_retry(max_attempts: int = 3, base_delay: float = 1.0):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    logging.warning(f"{fn.__name__} attempt {attempt} failed: {e}. Retrying in {delay}s")
                    await asyncio.sleep(delay)
        return wrapper
    return decorator
```

2. Apply to every FHIR post function in `events.py`:
```python
from app.utils.retry import with_retry

@with_retry(max_attempts=3)
async def _post_fhir_imaging_study(payload: dict):
    ...
```

3. Wire the existing `audit` DB writer to log every webhook event received and its outcome (success/failure/retry count) — use the same `AuditLog` model already present in the integration-hub's DB layer.

***

## Phase 3 — Service Registry: Make It Real

### 3.1 Registry Loader

**Where:** New `services/integration-hub/app/registry.py`

**Step-by-step:**

1. Write the loader:
```python
# services/integration-hub/app/registry.py
import json, pathlib, logging
from typing import Dict

logger = logging.getLogger(__name__)
SERVICES_ROOT = pathlib.Path(os.getenv("SERVICES_ROOT", "/services"))

_registry: Dict[str, dict] = {}

def load_registry() -> Dict[str, dict]:
    global _registry
    _registry = {}
    for manifest_path in SERVICES_ROOT.glob("*/openhis.service.json"):
        try:
            data = json.loads(manifest_path.read_text())
            _registry[data["name"]] = data
            logger.info(f"Registered service: {data['name']} @ {data.get('base_url')}")
        except Exception as e:
            logger.warning(f"Failed to load manifest {manifest_path}: {e}")
    return _registry

def get_registry() -> Dict[str, dict]:
    return _registry
```

2. Mount the services source tree into the integration-hub container in `docker-compose.yml`:
```yaml
integration-hub:
  volumes:
    - ./services:/services:ro   # read-only mount of all service directories
```

3. Call `load_registry()` inside the `lifespan` startup block.
4. Expose it via a new endpoint `GET /api/registry` (admin-only, JWT-protected) that returns the full manifest map — useful for tooling and debugging.

***

### 3.2 Platform Health Aggregator

**Where:** `services/integration-hub/app/routers/health.py`

**Step-by-step:**

1. Add a `GET /api/platform/status` endpoint that fans out concurrent health checks:
```python
import asyncio, httpx
from app.registry import get_registry

@router.get("/api/platform/status")
async def platform_status():
    registry = get_registry()
    results = {}

    async def check_service(name: str, svc: dict):
        url = f"{svc['base_url']}{svc.get('health_path', '/api/health')}"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                t0 = asyncio.get_event_loop().time()
                resp = await client.get(url)
                latency_ms = round((asyncio.get_event_loop().time() - t0) * 1000)
                results[name] = {
                    "status": "ok" if resp.status_code == 200 else "degraded",
                    "latency_ms": latency_ms,
                    "http_status": resp.status_code,
                }
        except Exception as e:
            results[name] = {"status": "unreachable", "error": str(e)}

    await asyncio.gather(*[check_service(n, s) for n, s in registry.items()])
    overall = "ok" if all(r["status"] == "ok" for r in results.values()) else "degraded"
    return {"overall": overall, "services": results}
```

2. This endpoint is the single pane of glass for an operator. Protect it with `require_token` but consider a dedicated `platform:admin` scope check.
3. Add a lightweight Traefik health check that polls `/api/platform/status` so the reverse proxy can surface degraded state to operators.

***

## Phase 4 — Code Quality \& Data Safety

### 4.1 Replace `_char_overlap` with Jaro-Winkler

**Where:** `services/mpi/app/matcher.py`

**Step-by-step:**

1. Add `jellyfish>=1.0` to `services/mpi/requirements.txt`.
2. Replace the function:
```python
# REMOVE:
def _char_overlap(a: str, b: str) -> float:
    ...

# ADD:
import jellyfish

def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return jellyfish.jaro_winkler_similarity(a.lower(), b.lower())
```

3. Update all call sites in `matcher.py` from `_char_overlap(...)` to `_name_similarity(...)`.
4. Run the existing `tests/test_matcher.py` — adjust expected scores since Jaro-Winkler will score transpositions differently from the old overlap function. The threshold of `0.70` may need slight recalibration. Add test cases for:

- Exact match → 1.0
- Transposition (`"John"` / `"Jhon"`) → ~0.97 (not 1.0 as before)
- Completely different names → < 0.5

***

### 4.2 Migrate MPI to PostgreSQL

**Where:** `services/mpi/app/db/`, `services/mpi/requirements.txt`, `docker-compose.yml`

**Step-by-step:**

1. Add dependencies to `requirements.txt`:
```
asyncpg>=0.29
sqlalchemy[asyncio]>=2.0
alembic>=1.13
```

2. Replace `aiosqlite` engine with `asyncpg`:
```python
# services/mpi/app/db/database.py
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("MPI_DATABASE_URL")  # postgresql+asyncpg://user:pass@postgres:5432/mpi

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

3. Add a `mpi` database to the Postgres service in `docker-compose.yml`:
```yaml
postgres:
  environment:
    POSTGRES_MULTIPLE_DATABASES: openmrs,mpi,keycloak
```

4. Initialise Alembic:
```bash
cd services/mpi
alembic init alembic
# Set sqlalchemy.url = ${MPI_DATABASE_URL} in alembic.ini
alembic revision --autogenerate -m "initial mpi schema"
alembic upgrade head
```

5. Run migrations at container startup via the `lifespan`:
```python
from alembic.config import Config
from alembic import command

@asynccontextmanager
async def lifespan(app):
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    yield
```


***

### 4.3 Implement A40 Patient Merge

**Where:** `services/hl7/app/handlers.py` — `handle_a40_merge()`

**Step-by-step:**

1. Parse the two patient identifiers from the A40 message (PID segment = surviving patient, MRG segment = deprecated patient):
```python
async def handle_a40_merge(msg) -> str:
    surviving_mrn = msg.PID.PID_3.CX_1.value
    deprecated_mrn = msg.MRG.MRG_1.CX_1.value
```

2. Call the MPI crossref API to merge identities:
```python
    mpi_url = os.getenv("MPI_BASE_URL")
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{mpi_url}/api/crossref/merge", json={
            "surviving_mrn": surviving_mrn,
            "deprecated_mrn": deprecated_mrn,
        })
        resp.raise_for_status()
```

3. POST a FHIR `$merge` operation to OpenMRS:
```python
    surviving_uuid = await _find_patient_uuid(surviving_mrn)
    deprecated_uuid = await _find_patient_uuid(deprecated_mrn)
    
    fhir_merge = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "source-patient", "valueReference": {"reference": f"Patient/{deprecated_uuid}"}},
            {"name": "target-patient", "valueReference": {"reference": f"Patient/{surviving_uuid}"}},
        ]
    }
    await _fhir_post("Patient/$merge", fhir_merge)
```

4. Publish `patient.merged` to the bus:
```python
    await bus.publish("patient.merged", {
        "surviving_mrn": surviving_mrn,
        "deprecated_mrn": deprecated_mrn,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return _make_ack(msg)
```

5. Add a test in `tests/hl7/test_a40.py` that mocks both the MPI and FHIR endpoints and asserts the merge calls are made with the correct identifiers.

***

### 4.4 Fix Deprecated Python/FastAPI Patterns

**Where:** All `main.py` files, `handlers.py`

**Step-by-step:**

**A. Replace `@app.on_event` with `lifespan`** (already shown in 2.1 above — apply this pattern to every service's `main.py`).

**B. Replace `datetime.utcnow()`:**

```bash
# Find all occurrences
grep -r "datetime.utcnow\(\)" services/

# Replace (or do it manually per file)
# BEFORE:
datetime.datetime.utcnow()
# AFTER:
datetime.now(timezone.utc)
```

Add `from datetime import datetime, timezone` where needed.

***

### 4.5 Structured JSON Logging

**Where:** All services' `main.py` / logging config

**Step-by-step:**

1. Add `python-json-logger>=2.0` to each service's `requirements.txt`.
2. Create `services/shared/logging_config.py` (or duplicate it per service if no shared lib exists):
```python
import logging
from pythonjsonlogger import jsonlogger

def configure_logging(service_name: str, level: str = "INFO"):
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level"},
        static_fields={"service": service_name},
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
```

3. Call it at the top of each `main.py`, before `app = FastAPI(...)`:
```python
from app.logging_config import configure_logging
configure_logging("mpi", os.getenv("LOG_LEVEL", "INFO"))
```

4. Replace all bare `print()` statements found throughout the codebase with proper `logger.info()` / `logger.error()` calls.

***

## Phase 5 — CI/CD \& Observability

### 5.1 GitHub Actions CI Pipeline

**Where:** New `.github/workflows/ci.yml`

**Full pipeline:**

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    
    services:
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_PASSWORD: testpass
          POSTGRES_DB: mpi_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 3s
          --health-retries 5

    env:
      MPI_DATABASE_URL: postgresql+asyncpg://postgres:testpass@localhost:5432/mpi_test
      REDIS_URL: redis://localhost:6379
      OPENMRS_USER: test
      OPENMRS_PASS: test
      FHIR_BASE_URL: http://localhost:9999  # mocked in tests

    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      
      - name: Install dependencies (all services)
        run: |
          for svc in services/mpi services/hl7 services/integration-hub services/analytics services/ris; do
            pip install -r $svc/requirements.txt
          done
          pip install pytest pytest-asyncio pytest-cov httpx
      
      - name: Run linting
        run: |
          pip install ruff
          ruff check services/
      
      - name: Run tests
        run: |
          pytest services/mpi/tests/ \
                 services/hl7/tests/ \
                 services/integration-hub/tests/ \
                 services/analytics/tests/ \
                 --cov=services \
                 --cov-report=xml \
                 -v
      
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml

  docker-build:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4
      - name: Build all service images
        run: |
          for svc in mpi hl7 integration-hub analytics ris ai-controller; do
            docker build services/$svc -t openhis-$svc:ci
          done
```


***

### 5.2 Odoo as First-Class Bus Citizen

**Where:** `services/integration-hub/app/worker.py`

**Step-by-step:**

1. Extract Odoo sync into its own function with independent retry:
```python
@with_retry(max_attempts=3, base_delay=2.0)
async def sync_patient_to_odoo(patient_data: dict) -> dict:
    return await odoo.upsert_patient(patient_data)
```

2. Run it as an independent step after the OpenELIS sync, not nested inside it:
```python
async def _sync_patient(patient: dict):
    # Step 1: OpenELIS
    try:
        await sync_patient_to_elis(patient)
        await bus.publish("patient.synced", {"mrn": patient["mrn"], "target": "elis"})
    except Exception as e:
        logger.error(f"OpenELIS sync failed for {patient['mrn']}: {e}")

    # Step 2: Odoo — independent, does not depend on OpenELIS success
    try:
        await sync_patient_to_odoo(patient)
        await bus.publish("odoo.patient.synced", {
            "mrn": patient["mrn"],
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        await _write_audit("odoo_sync", patient["mrn"], "success")
    except Exception as e:
        logger.error(f"Odoo sync failed for {patient['mrn']}: {e}")
        await _write_audit("odoo_sync", patient["mrn"], "failed", error=str(e))
```

3. Add `odoo.patient.synced` as a subscribed event in Odoo's `openhis.service.json` manifest (even though Odoo doesn't run a Python consumer — document it so future tooling can model the full dependency graph).
4. Add analytics consumer handler for `odoo.patient.synced` → increment billing-sync counters.

***

## Delivery Checklist

Once all phases are complete, validate the following end-to-end before marking the work done:

- [ ] All services start with a missing env var and immediately exit with a clear `FATAL` message
- [ ] An unauthenticated request to `/api/patients` returns 401
- [ ] An HL7 A01 admit triggers: MPI record creation → `patient.synced` on bus → analytics consumer acknowledges → Odoo upsert
- [ ] An Orthanc DICOM store webhook triggers: FHIR ImagingStudy POST → `dicom.stored` on bus → AI controller consumer acknowledges and starts inference
- [ ] `GET /api/platform/status` returns live health of all registered services
- [ ] An HL7 A40 merge successfully calls the MPI merge API and the OpenMRS FHIR `$merge` endpoint
- [ ] CI pipeline runs green on a clean branch with all tests passing
- [ ] No `datetime.utcnow()` or `@app.on_event` warnings appear in service logs
- [ ] All service logs are JSON-formatted and include the `service` field
