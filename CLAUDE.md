# CLAUDE.md — OpenHIS


## Project Overview


OpenHIS is an **open-source, profile-driven Health Information Platform**.  
It orchestrates and integrates best-of-breed clinical systems (OpenMRS, OpenELIS, Odoo, Orthanc, OHIF) rather than replacing them.  
The integration spine is **FHIR R4** (data contract) + **Redis Streams** (event bus); Keycloak handles all authentication.


> **Before writing any code, read:** @docs/explaining-the-project/concepts.md  
> It defines what OpenHIS is, what it is not, and the three governing principles.  
> Architecture deep-dives: @docs/explaining-the-project/architecture.md  
> ADRs (event bus, FHIR adapter, MPI spine): @docs/adr/


---


## Stack


| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Framework | FastAPI (all services) |
| Auth | Keycloak (OIDC) + JWT via `python-jose` |
| Event bus | Redis Streams (consumer groups) |
| Database | PostgreSQL (per-service) |
| Reverse proxy | Nginx (NJS JWT validation) |
| Containers | Docker Compose with profile system |
| Shared SDK | `libs/openhissdk` |
| Platform CLI | `platform/opm.py` (OPM) |
| HTTP mocking | `respx` |
| Test runner | `pytest` |


---


## Repository Layout

```
compose/          Docker Compose files (base + per-profile overrides)
  base.yml        Always-on core stack
  profiles/       emr.yml  laboratory.yml  imaging.yml  erp.yml  analytics.yml
  overrides/      production.yml  ci.yml
libs/
  openhissdk/     Shared Python SDK — canonical source for auth, bus, logging, retry
platform/         OPM CLI (opm.py) + profile/nginx engines — installable via pip
services/         FastAPI microservices
  admin/          Core — always deployed; the admin & operations plane
  mpi/            Core — Master Patient Index (identity spine)
  integration-hub/Core — FHIR adapter hub; all cross-system calls live here
  hl7/            Core — MLLP HL7 listener/dispatcher
  fhir-bridge/    Core — FHIR translator
  ris/            imaging profile — Radiology IS
  ai-controller/  imaging profile — AI inference orchestration
  analytics/      analytics profile — metrics collector
  patient-portal/ analytics profile — patient-facing proxy
  simulator/      DICOM study generator (dev/demo)
  legacy/         ⚠ FROZEN — ehr, lis, pharmacy: do NOT extend or add routes
infra/            Third-party configs (nginx, keycloak, orthanc, openelis, ohif, odoo)
pipelines/        AI pipeline workers (poc-ct, poc-xray)
tests/
  unit/           No Docker, no network; ~2 min; run on every commit
  integration/    respx HTTP mocks; ~5 min; run on every PR
  smoke/          Full Docker stack; ~15 min; run on merge to main
docs/
  concepts.md                      ← What OpenHIS is and why (start here)
  explaining-the-project/          Architecture, adapter/service/profile contracts
  adr/                             Architectural Decision Records
  guidelines-for-contributors/     CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
  task-planning/                   Active TODO list and phase plans
```

---


## Dev Environment Setup


```bash
# 1. Clone and create venv
python -m venv venv && source venv/bin/activate

# 2. Install shared SDK and OPM in editable mode
pip install -e libs/openhissdk
pip install -e platform

# 3. Install all service dev dependencies
pip install -r requirements-dev.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — replace ALL CHANGEMEBEFOREDEPLOY values before starting

# 5. Initialise platform and start base stack
python platform/opm.py init
OPENHIS_PROFILES=emr make up
```


---


## Key Commands


### Stack management
```bash
make up                          # Start stack (respects OPENHIS_PROFILES)
make health                      # Check all service health endpoints
OPENHIS_PROFILES=imaging make up # Start with imaging profile

python platform/opm.py --help
python platform/opm.py init
python platform/opm.py enable emr laboratory
python platform/opm.py disable erp
python platform/opm.py status
```


### Tests (preferred invocations)
```bash
# Unit — no Docker needed
pytest tests/unit -q --tb=short

# Integration — no Docker needed (respx mocks)
pytest tests/integration -q --tb=short

# Smoke — requires full Docker stack running
pytest tests/smoke -q

# Target a single service
pytest tests/mpi -x -q --tb=short
pytest tests/ris/test_orders.py::test_create_order -x -s --tb=long

# All tests
pytest tests -q --tb=short
```


**Rule:** run targeted tests during development. Run `tests/unit` + `tests/integration` before any PR.  
Never run `tests/smoke` unless the full Docker stack is up.


---


## Service Contract — every service must have


- `main.py` — FastAPI app with `lifespan` context (not deprecated `@app.on_event`)
- `openhis.service.json` — manifest: name, version, profile, port, bus topics, required env vars
- `Dockerfile`
- `routers/` — FastAPI router modules
- Tests in `tests/unit/<service>/` or `tests/integration/`


Add a service with OPM scaffolding:
```bash
python platform/opm.py add-service my-service --profile analytics --port 8099
```


Full contract: @docs/explaining-the-project/adding-a-module.md


---


## Code Style & Conventions


### Python
- Python 3.11; type hints required on all public functions and router handlers
- 4-space indentation, PEP 8 naming (`snake_case` functions, `PascalCase` Pydantic models)
- f-strings preferred; Pydantic v2 for all request/response schemas
- `datetime.now(timezone.utc)` — never `datetime.utcnow()` (deprecated)
- No bare `except:` — always catch specific exceptions


### Authentication — use the SDK
```python
from openhissdk.auth import require_token, require_roles

@router.get("/protected")
async def endpoint(claims: dict = Depends(require_token)):
    ...

# OR as global middleware
from openhissdk.auth import JWTMiddleware
app.add_middleware(JWTMiddleware)
```
**Never** create or edit `jwtauth.py` outside `libs/openhissdk/`.


### Logging — use the SDK
```python
from openhissdk.logging import configure
configure("my-service")   # call once in main.py
log = logging.getLogger("my-service")
log.info("event happened", extra={"patient_id": mrn})
```
All logs must be JSON-formatted and include the `service` field.  
**Never** create or edit `logconfig.py` outside `libs/openhissdk/`.


### Redis event bus — use the SDK
```python
from openhissdk.bus import publish, consume
await publish("patient.synced", {"mrn": mrn, "ts": ...})
```


### Retry
```python
from openhissdk.retry import with_retry

@with_retry(attempts=3, backoff=2.0)
async def call_external():
    ...
```


### Startup env-var guard — required in every service
```python
import sys, os
REQUIRED_ENV = ["KEYCLOAK_URL", "POSTGRES_DSN", ...]
missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
if missing:
    sys.exit(f"FATAL: missing required env vars: {missing}")
```


---


## Integration Rules (read before touching integration-hub)


- **No service calls another service's internal API directly.** All cross-system data flows go through `integration-hub/app/services/<app>.py` adapters.
- Adapters implement `upsert_patient`, `get_patient`, and `healthcheck` as async functions (see @docs/explaining-the-project/adapter-contract.md).
- After every successful sync, the hub publishes an event to the bus. Adding a new flow means publishing a new event, not wiring a new HTTP call.
- Every cross-system write must produce an entry in the hub audit log.


---


## Bus Events Reference


| Event | Producer | Consumers |
|---|---|---|
| `patient.synced` | mpi | integration-hub, analytics, hl7 |
| `lab.order.routed` | integration-hub | analytics |
| `lab.result.ready` | integration-hub | analytics, hl7 |
| `dicom.stored` | integration-hub | ai-controller, analytics |
| `radiology.report.ready` | ris | analytics |
| `ai.result.ready` | ai-controller | ris (save-back) |
| `odoo.patient.synced` | integration-hub | analytics |


---


## Profiles Reference


| Profile | Extra services | RAM estimate |
|---|---|---|
| `base` (always on) | postgres, redis, nginx, keycloak, mpi, integration-hub, hl7, admin | ~512 MB |
| `emr` | OpenMRS | +2 GB |
| `laboratory` | OpenELIS | +1 GB |
| `imaging` | Orthanc, OHIF, RIS, AI controller | +1.5 GB |
| `erp` | Odoo | +1 GB |
| `analytics` | analytics service, patient-portal | +256 MB |


---


## Git & PR Workflow


- Default branch: `main`
- Branch naming: `feat/short-desc`, `fix/issue-123`, `docs/topic`, `refactor/scope`
- Commit messages — Conventional Commits:
  ```
  type(scope): short summary

  Types: feat  fix  docs  refactor  test  chore  security
  ```
- PR checklist:
  - `pytest tests/unit tests/integration` passes with no failures
  - No new `jwtauth.py` or `logconfig.py` outside `libs/openhissdk/`
  - Required env vars declared in `openhis.service.json` under `env.required`
  - New env vars added to `.env.example` with a comment
  - `openhis.service.json` updated if ports, paths, or bus topics changed
  - `CHANGELOG.md` updated under `Unreleased`
  - No hardcoded passwords, tokens, or connection strings


---


## How I Want You To Work (Claude)


**Before making changes:**
- Read @docs/concepts.md to decide whether the work belongs in the platform or in a module
- Check the governing principle: integration over reimplementation → contracts over direct calls → platform-first
- If the change adds a service-to-service HTTP call, rethink: it should be an adapter + event


**When coding:**
- Propose a short plan before multi-file refactors or cross-service changes
- Work in small, reviewable steps; prefer minimal diffs
- Modify only files relevant to the current request; keep existing style
- Check `openhis.service.json` when touching ports, env vars, or bus topics
- Run `pytest tests/<service> -x -q --tb=short` before declaring work done


**Hard rules:**
- Do NOT touch `services/legacy/` (ehr, lis, pharmacy) — FROZEN
- Do NOT create per-service `jwtauth.py` or `logconfig.py` — use `openhissdk`
- Do NOT use `datetime.utcnow()` or `@app.on_event()`
- Do NOT hand-edit `infra/nginx/nginx.conf` — regenerate via `python platform/nginxgen.py`
- Do NOT add direct HTTP calls between native services — use the bus or the adapter hub


**Quota hygiene:**
- Read files only when you need to modify or directly reference them — do not speculatively scan the repo
- Do not list directory trees unless asked; request the exact path from the user if unsure
- Do not re-read `CLAUDE.md` or `@docs/concepts.md` mid-session — they are already in context
- When reviewing a change, prefer a `git diff` snippet over re-reading full files
- For symbol or text searches, ask the user to run `rg <pattern>` and paste the output rather than scanning files yourself
- Start a fresh conversation for each unrelated task — do not carry unrelated history forward


---


## Gotchas & Warnings


- **Keycloak runs in `start-dev` mode** by default — never use the base stack in production; use `compose/overrides/production.yml`
- **In-memory dedup sets** in `integration-hub/worker.py` (`synced_patients`, `synced_orders`) do not survive container restarts — tracked for Redis SADD migration
- **`CHANGEMEBEFOREDEPLOY`** in `.env` is a security hazard; the startup guard catches missing vars but not weak passwords
- **`infra/nginx/nginx.conf`** is generated from `nginx.conf.j2` by `nginxgen.py` — manual edits are overwritten on next OPM command
- **Some services still have local `jwtauth.py` / `logconfig.py`** — being migrated; CI fails if new ones are added outside `libs/`
- **Integration tests use `respx`** for HTTP mocking — mock at the HTTP boundary only, never at the adapter layer
- **Redis dedup keys** need a TTL (7-day `EXPIRE`) to prevent unbounded growth — not yet implemented everywhere


---


## Additional References


- **Concepts & Goals (start here):** @docs/concepts.md
- Architecture: @docs/explaining-the-project/architecture.md
- Profiles system: @docs/explaining-the-project/profiles.md
- Adding a module: @docs/explaining-the-project/adding-a-module.md
- Adapter contract: @docs/explaining-the-project/adapter-contract.md
- Service contract: @docs/explaining-the-project/service-contract.md
- Security policy: @docs/guidelines-for-contributors/SECURITY.md
- Contributing guide: @docs/guidelines-for-contributors/CONTRIBUTING.md
