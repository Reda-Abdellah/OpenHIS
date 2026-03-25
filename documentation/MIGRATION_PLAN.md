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

### Phase 2 — OpenELIS Global 2  ⬜ PENDING
**Goal:** Stand up OpenELIS alongside the existing stack. No wiring yet.

**Plan:**
- Add `openelis-db` (PostgreSQL, `clinlims` DB) + `openelis` service to `docker-compose.yml`
- Image: `ghcr.io/i-tech-uw/openelis-global-2:latest`
- Add nginx location block for `/openelis/`
- Add Makefile targets: `openelis-up`, `openelis-verify`
- Write `scripts/verify_openelis.py` — checks health, REST API, FHIR R4 endpoint

**Key endpoints:**
- FHIR R4: `/fhir/R4/`
- REST API: `/api/`
- Admin UI: `/`

---

### Phase 3 — Odoo 17 Community  ⬜ PENDING
**Goal:** Stand up Odoo alongside the existing stack. No wiring yet.

**Plan:**
- Add `odoo-db` (PostgreSQL) + `odoo` service to `docker-compose.yml`
- Image: `odoo:17.0`
- Add nginx location block for `/odoo/`
- Add Makefile targets: `odoo-up`, `odoo-verify`
- Install modules: `sale`, `stock`, `account`, `medical` (if available)

**Key endpoints:**
- XML-RPC: `/xmlrpc/2/` (legacy integration)
- REST: `/api/` (Odoo 17+)
- Web UI: `/web`

---

### Phase 4 — Integration Hub  ⬜ PENDING
**Goal:** Build `integration-hub` FastAPI service that replaces `fhir-bridge`. Implements patient sync and lab order/result round-trips between OpenMRS and OpenELIS.

**Plan:**
- New service `services/integration-hub` (FastAPI, Python)
- Polls OpenMRS AtomFeed for patient and order events
- Routes lab orders from OpenMRS → OpenELIS via FHIR R4 (`ServiceRequest`)
- Routes lab results from OpenELIS → OpenMRS via FHIR R4 (`DiagnosticReport`)
- Routes pharmacy orders from OpenMRS → Odoo via XML-RPC or REST
- Replaces `fhir-bridge` in `docker-compose.yml` and nginx

**Key integration patterns:**
- **OpenMRS AtomFeed** (`/openmrs/ws/atomfeed/`) — pull-based event stream for patient/encounter/order changes
- **OpenELIS FHIR R4** — accepts `ServiceRequest` for lab orders, returns `DiagnosticReport`
- **Odoo XML-RPC** (`/xmlrpc/2/object`) — `sale.order` for billing, `stock.picking` for dispensing

---

### Phase 5 — Full Cutover  ⬜ PENDING
**Goal:** All clinical data flows through OpenMRS/OpenELIS/Odoo. Remove redundant custom services.

**Plan:**
- Migrate any remaining data from `ehr.db`, `lis.db`, `pharmacy.db`, `mpi.db` to OpenMRS/OpenELIS/Odoo
- Update `patient-portal` to read from OpenMRS REST instead of `ehr`
- Update `analytics` to aggregate from OpenMRS/OpenELIS REST APIs
- Update `hl7` service to wire incoming HL7 messages to OpenMRS REST/FHIR
- Remove services from `docker-compose.yml`: `ehr`, `lis`, `pharmacy`, `mpi`, `admin`
- Remove their volumes and nginx location blocks

---

### Phase 6 — Polish & RIS Adapter  ⬜ PENDING
**Goal:** Clean up, slim down RIS, add retry/audit to integration-hub, write DEMO.md.

**Plan:**
- Slim `ris` to a thin adapter: radiology orders from OpenMRS → Orthanc, results → OpenMRS FHIR
- Add retry queue and audit log to `integration-hub`
- Write `documentation/DEMO.md` with step-by-step walkthrough
- Final smoke test across all integration paths

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
