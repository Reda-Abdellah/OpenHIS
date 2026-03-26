# OpenHIS — Migration Plan & Project Context

> Created: 2026-03-25
> Purpose: Context document for future sessions — captures objectives, architecture decisions, and the phased migration roadmap.

---

## Project Objective

OpenHIS started as a fully custom Python/FastAPI microservices stack (12 services) mimicking a hospital information system. The goal has shifted: **replace custom implementations with battle-tested open-source clinical systems** while keeping the lightweight Python/FastAPI/Docker philosophy.

The inspiration is [Bahmni](https://github.com/Bahmni/bahmni-core) — a production HIS that integrates OpenMRS, OpenELIS, and Odoo as best-of-breed modules rather than reinventing them. OpenHIS follows the same modular approach but stays Docker-native and avoids the Java/Spring complexity of Bahmni core.

### Design Principles
- **No redundant custom code** where a maintained open-source system exists
- **FHIR R4** as the integration lingua franca between modules
- **Docker Compose** as the single deployment mechanism
- **Thin Python adapters** only where glue logic is genuinely needed
- Existing services stay running during migration — each phase adds, then eventually removes

---

## Current Stack (Pre-Migration Baseline)

| Service | Port | Tech | Role |
|---------|------|------|------|
| ehr | 8003 | FastAPI + SQLite | Patient, encounters, orders, CDSS |
| lis | 8004 | FastAPI + SQLite | Lab orders and results |
| ris | 8002 | FastAPI + SQLite | Radiology orders, links to Orthanc |
| pharmacy | 8006 | FastAPI + SQLite | Dispensing and medication tracking |
| mpi | 8007 | FastAPI + SQLite | Master Patient Index |
| admin | 8011 | FastAPI + SQLite | System administration UI |
| fhir-bridge | 8005 | FastAPI | FHIR routing between services |
| hl7 | 8009 | FastAPI + SQLite | HL7 v2 MLLP (port 2575) |
| analytics | 8008 | FastAPI + SQLite | Metrics aggregation |
| patient-portal | 8010 | FastAPI + SQLite | Patient-facing portal |
| ai-controller | 8000 | FastAPI + SQLite | AI pipeline orchestration |
| simulator | 8001 | FastAPI | DICOM study simulation |
| orthanc | 8042 | C++ (DICOM) | PACS — DICOM storage and DICOMweb |
| ohif | — | React/nginx | DICOM viewer |
| nginx | 80 | nginx | Reverse proxy / portal entry point |
| postgres | 5432 | PostgreSQL 15 | Orthanc backend DB |

---

## Target Architecture (Post-Migration)

| Component | Replaces | Role |
|-----------|----------|------|
| OpenMRS O3 | ehr, mpi, admin (partial) | EMR — patients, encounters, orders, clinical data |
| OpenELIS Global 2 | lis | Laboratory information system |
| Odoo 17 Community | pharmacy, admin (billing) | ERP — pharmacy dispensing, billing, inventory |
| integration-hub | fhir-bridge | Thin FastAPI service — event routing between OpenMRS, OpenELIS, Odoo |
| ris (slimmed) | — | Thin adapter: radiology orders → Orthanc, results → OpenMRS |
| orthanc | — | Kept as-is (PACS) |
| ohif | — | Kept as-is (DICOM viewer) |
| ai-controller | — | Kept as-is (AI pipeline) |
| hl7 | — | Kept (HL7 MLLP bridge), eventually wire to OpenMRS AtomFeed |
| analytics | — | Kept, wire to OpenMRS/OpenELIS REST APIs |
| patient-portal | — | Kept, eventually read from OpenMRS REST |
| simulator | — | Kept (DICOM simulation) |

**Services to delete after full cutover:** `ehr`, `lis`, `pharmacy`, `mpi`, `admin` (custom ones)

---

## Phased Migration Roadmap

### Phase 1 — OpenMRS O3  ✅ COMPLETE
**Goal:** Stand up OpenMRS O3 alongside the existing stack. Zero disruption to running services.

**What was done:**
- Added `openmrs-db` (MySQL 8) + `openmrs` (backend) + `openmrs-frontend` (SPA) to `docker-compose.yml`
- Added volumes: `openmrs-mysql`, `openmrs-data`
- Updated `infra/nginx/nginx.conf` with upstreams and location blocks for `/openmrs/` and `/openmrs/spa/`
- Added Makefile targets: `openmrs-up`, `openmrs-logs`, `openmrs-seed`, `openmrs-verify`, `openmrs-clean`
- Created `scripts/seed_openmrs.py` — creates demo location + patient with valid Luhn-Mod-N OpenMRS ID
- Created `scripts/verify_openmrs.py` — 6 acceptance checks (health, REST auth, FHIR2, patient search, FHIR patient, FHIR bundle)

**Key lessons learned:**
- OpenMRS image uses `OMRS_DB_HOSTNAME` / `OMRS_DB_NAME` / `OMRS_DB_USERNAME` / `OMRS_DB_PASSWORD` (NOT `DB_HOST` etc.)
- MySQL must have `--log_bin_trust_function_creators=1` for stockmanagement module triggers
- `--lower_case_table_names=1` must be set at first MySQL init (can't change after)
- Health check should target `/openmrs/login.htm` not `/openmrs/health/started` (the latter returns 503 during all of init)
- First boot runs ~1707 Liquibase changesets; takes 10-15 min; InitializationFilter has a logging bug that can stall it — a `docker compose restart openmrs` on second boot bypasses it
- OpenMRS ID requires Luhn-Mod-N checkdigit with charset `0123456789ACDEFGHJKLMNPRTUVWXY`

**Acceptance criteria (all passed):**
1. ✓ `/openmrs/health/started` returns HTTP 200
2. ✓ REST API authenticates (`admin` / `Admin123`)
3. ✓ FHIR2 CapabilityStatement reachable (fhirVersion=4.0.1)
4. ✓ Patient search returns results
5. ✓ FHIR R4 Patient by UUID works
6. ✓ FHIR R4 Patient search returns Bundle (total=101)

**Access:**
- UI: `http://localhost/openmrs/spa`
- REST: `http://localhost/openmrs/ws/rest/v1/`
- FHIR R4: `http://localhost/openmrs/ws/fhir2/R4/`
- Credentials: `admin` / `Admin123`

---

### Phase 2 — OpenELIS Global 2  🔄 IN PROGRESS
**Goal:** Stand up OpenELIS alongside the existing stack. No wiring yet.

**What was done:**
- Added `openelis-db` (PostgreSQL 14, `clinlims` DB) + `openelis` service to `docker-compose.yml`
- Added volumes: `openelis-pg`, `openelis-lucene`
- Image: `itechuw/openelis-global-2:latest` (DockerHub — the ghcr.io mirror is not the primary distribution)
- CATALINA_OPTS used to configure datasource URL/user/password pointing to `openelis-db`
- OpenELIS exposed on host port **8082** (HTTP) for direct UI/API access
- Added nginx upstream `openelis_be` and location block for `/openelis/` (path-stripping proxy — works for FHIR/REST calls; HTML UI navigation requires using port 8082 directly)
- Added Makefile targets: `openelis-up`, `openelis-logs`, `openelis-verify`, `openelis-clean`
- Created `scripts/verify_openelis.py` — 5 acceptance checks

**Key notes:**
- OpenELIS does NOT have a built-in context path (unlike OpenMRS `/openmrs`); it serves from root `/`
- First boot runs Liquibase migrations against PostgreSQL; expect 3–5 min before healthy
- Default credentials: `admin` / `adminADMIN!`
- Liquibase contexts: production (no `-Dspring.liquibase.contexts=test` needed for real data)

**Access:**
- UI (direct): `http://localhost:8082/`
- FHIR R4: `http://localhost:8082/fhir/R4/`
- REST API: `http://localhost:8082/api/`
- Via nginx (FHIR/REST only): `http://localhost/openelis/fhir/R4/`

**Acceptance criteria:**
1. ⬜ `GET /fhir/R4/metadata` returns HTTP 200
2. ⬜ CapabilityStatement has `fhirVersion=4.0.1`
3. ⬜ Authenticated FHIR R4 Patient query returns a Bundle
4. ⬜ REST API endpoint reachable
5. ⬜ Admin UI root page reachable

---

### Phase 3 — Odoo 17 Community  🔄 IN PROGRESS
**Goal:** Stand up Odoo alongside the existing stack. No wiring yet.

**What was done:**
- Added `odoo-db` (PostgreSQL 15) + `odoo` (Odoo 17.0) services + 2 volumes
- Created `infra/odoo/odoo.conf` — sets `proxy_mode = True`, DB connection, master password
- Odoo exposed on host port **8069** for direct UI access
- Added nginx upstream `odoo_be` + `/odoo/` location (path-stripping) + `/odoo/longpolling/` (WebSocket upgrade for gevent worker)
- Added Makefile targets: `odoo-up`, `odoo-logs`, `odoo-verify`, `odoo-clean`
- Created `scripts/verify_odoo.py` — 5 acceptance checks

**Key notes:**
- `POSTGRES_DB=postgres` intentionally; Odoo creates its own app database via the "Create Database" web page
- First boot: visit `http://localhost:8069/web/database/manager` and create DB named `odoo` with master password `admin`
- Install modules: Sale, Inventory, Accounting (needed for Phase 4 pharmacy integration)
- Default admin credentials: `admin` / `admin` (set during DB creation)

**Access:**
- Web UI (direct): `http://localhost:8069/web`
- XML-RPC: `http://localhost:8069/xmlrpc/2/`
- REST (Odoo 17): `http://localhost:8069/api/`
- Via nginx (XML-RPC/REST only): `http://localhost/odoo/xmlrpc/2/`

**Acceptance criteria:**
1. ⬜ `GET /web/health` returns `{"status": "pass"}`
2. ⬜ XML-RPC `common.version()` returns `server_version=17.x`
3. ⬜ XML-RPC `db.list()` is reachable
4. ⬜ Root UI reachable (HTTP 200 after redirect)
5. ⬜ Admin can authenticate via XML-RPC (requires DB to exist)

---

### Phase 4 — Integration Hub  🔄 IN PROGRESS
**Goal:** Build `integration-hub` FastAPI service that wires OpenMRS, OpenELIS, and Odoo. Runs alongside `fhir-bridge` (which is removed in Phase 5).

**What was done:**
- Created `services/integration-hub/` — FastAPI service on port 8012
- Background polling worker (`app/worker.py`) runs every `POLL_INTERVAL_S` seconds:
  - **Patient sync**: OpenMRS FHIR → OpenELIS FHIR (idempotent upsert by identifier)
  - **Lab order routing**: OpenMRS `ServiceRequest` → OpenELIS `ServiceRequest`
  - **Result routing**: OpenELIS final `DiagnosticReport` → OpenMRS FHIR
- `app/services/openmrs.py` — FHIR R4 client (patients, ServiceRequests, DiagnosticReports)
- `app/services/openelis.py` — FHIR R4 client (upsert patient, create ServiceRequest, fetch reports)
- `app/services/odoo.py` — XML-RPC client wrapped in `asyncio.to_thread` (pharmacy sale.order)
- `GET /api/health` — reports upstream status for OpenMRS, OpenELIS, Odoo
- `GET /api/atomfeed/status` — sync counters + last poll timestamp
- `POST /api/atomfeed/trigger` — manual sync trigger
- Added to docker-compose.yml; nginx `/integration-hub/` location block
- Added Makefile targets: `hub-up`, `hub-logs`, `hub-verify`, `hub-clean`
- Created `scripts/verify_hub.py` — 6 acceptance checks

**Key notes:**
- `fhir-bridge` stays running in Phase 4 — existing custom services (ehr, lis, pharmacy) still call it directly via Docker DNS
- Integration hub is additive: it syncs the open-source tier without touching the custom tier
- In Phase 5, `fhir-bridge` env vars in custom services will be updated to point to `integration-hub`
- Pharmacy order routing to Odoo requires `ODOO_DB` to exist; if Odoo is not initialized, pharmacy sync is skipped gracefully

**Acceptance criteria:**
1. ⬜ `GET /api/health` returns HTTP 200
2. ⬜ Service status is `ok` or `degraded`
3. ⬜ OpenMRS upstream shows `up`
4. ⬜ OpenELIS upstream shows `up`
5. ⬜ `GET /api/atomfeed/status` returns counters
6. ⬜ `POST /api/atomfeed/trigger` returns `{"status": "triggered"}`

**Key integration patterns:**
- **Polling**: FHIR `_sort=-_lastUpdated&_count=N` replaces AtomFeed cursor tracking for simplicity; fully idempotent
- **OpenELIS FHIR R4** — accepts `ServiceRequest`, returns `DiagnosticReport`
- **Odoo XML-RPC** (`/xmlrpc/2/object`) — `sale.order` for pharmacy dispensing

---

### Phase 5 — Full Cutover  ✅ COMPLETE
**Goal:** All clinical data flows through OpenMRS/OpenELIS/Odoo. Remove redundant custom services.

**What was done:**

**Service code changes:**
- `patient-portal/routers/auth.py` — login now validates MRN+DOB against OpenMRS FHIR Patient instead of EHR
- `patient-portal/routers/me.py` — all data endpoints rewritten to use OpenMRS FHIR (Patient, Encounter, Condition, AllergyIntolerance) and OpenELIS FHIR (DiagnosticReport); RIS stays for imaging
- `analytics/collector.py` — rewritten to use OpenMRS FHIR counts and OpenELIS DiagnosticReport counts; AI and RIS unchanged
- `hl7/handlers.py` — all handlers rewritten to use OpenMRS FHIR R4 for patient upsert and Encounter creation; ORU^R01 posts DiagnosticReport to OpenMRS
- `integration-hub/app/routers/events.py` — added legacy event handlers (report-final, dicom-stored, ai-job-completed) that push to OpenMRS FHIR; replaces fhir-bridge event endpoints
- `integration-hub/app/translators/` — copied diagnostic_report, imaging_study, observation translators from fhir-bridge

**Infrastructure changes:**
- `docker-compose.yml`:
  - **Removed services**: `ehr`, `lis`, `pharmacy`, `mpi`, `admin`, `fhir-bridge`
  - **Removed volumes**: `admin-data`, `ehr-data`, `lis-data`, `pharmacy_data`, `mpi_data`
  - `analytics`, `hl7`, `patient-portal` env vars updated to point to OpenMRS/OpenELIS
  - `orthanc`, `ris`, `ai-controller` `FHIR_BRIDGE_URL` updated to `http://integration-hub:8012`
- `nginx.conf`:
  - **Removed upstreams**: `ehr`, `lis`, `fhirbridge`, `admin`, `mpi`, `pharmacy`
  - **Removed location blocks**: `/ehr/`, `/lis/`, `/fhir-bridge/`, `/admin/`, `/mpi/`, `/pharmacy/`
- `scripts/migrate_to_openmrs.py` — migration script for ehr.db → OpenMRS FHIR and lis.db → OpenELIS FHIR
- `Makefile`: added `phase5-migrate` target

**Migration procedure:**
1. `make phase5-migrate` — migrate data from legacy SQLite DBs (run while old services still have volumes)
2. `docker compose up -d --build` — start new stack (old services absent)
3. Old volumes are orphaned; remove manually with `docker volume rm` after verifying data

---

### Phase 6 — Polish & RIS Adapter  ✅ COMPLETE
**Goal:** Clean up, slim down RIS, add retry/audit to integration-hub, write DEMO.md.

**Delivered:**
- `services/ris/openmrs_sync.py` — background worker polls OpenMRS FHIR for imaging
  `ServiceRequest` resources; auto-registers patients and creates RIS orders
- `services/ris/main.py` — v3.3.0; starts `sync_loop()` on startup
- `services/integration-hub/app/db/audit.py` — `aiosqlite`-backed audit log;
  every sync event (success / failure / retry) persisted to `/data/hub-audit.db`
- `services/integration-hub/app/worker.py` — retry queue with exponential back-off
  (BASE=15 s, max 5 attempts); `_drain_retries()` called each poll cycle
- `services/integration-hub/app/routers/audit.py` — `GET /api/audit` with
  `limit`, `offset`, `event_type`, `resource_type` query filters
- `documentation/DEMO.md` — full step-by-step walkthrough of all integration paths

---

## Key Technical References

### OpenMRS O3
- Backend image: `openmrs/openmrs-reference-application-3-backend:nightly`
- Frontend image: `openmrs/openmrs-reference-application-3-frontend:nightly`
- DB: MySQL 8, flags: `--character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci --lower_case_table_names=1 --log_bin_trust_function_creators=1`
- Env vars: `OMRS_DB_HOSTNAME`, `OMRS_DB_PORT`, `OMRS_DB_NAME`, `OMRS_DB_USERNAME`, `OMRS_DB_PASSWORD`, `OMRS_CREATE_TABLES`, `OMRS_AUTO_UPDATE_DATABASE`, `OMRS_ADMIN_USER_PASSWORD`
- OpenMRS ID format: base digits + Luhn-Mod-N checkdigit, charset `0123456789ACDEFGHJKLMNPRTUVWXY`

### OpenELIS Global 2
- Image: `ghcr.io/i-tech-uw/openelis-global-2:latest`
- DB: PostgreSQL (`clinlims` database)
- FHIR R4: `/fhir/R4/`

### Odoo 17
- Image: `odoo:17.0`
- DB: PostgreSQL
- XML-RPC: `/xmlrpc/2/common` and `/xmlrpc/2/object`
- REST (v17): `/api/`

### Bahmni Reference
- Cloned at: `/home/reda/bahmani/bahmni-core`
- Integration pattern: OpenMRS core + AtomFeed publisher → subscribers (OpenELIS, Odoo)
- OpenHIS differs: Docker-native, no Java/Spring, Python adapters instead of Java subscribers
