# OpenHIS — Feature Inventory & Fulfilled Requirements

> Analysis date: 2026-03-26
> Codebase: /home/reda/OpenHIS

---

## Platform Layer

The platform layer is always-on regardless of which profiles are active. It provides deployment management, identity, messaging, and cross-service coordination.

### OPM CLI (`platform/opm.py`)

- `opm init` — interactive first-run wizard: profile selection, passwords, `.env` generation, nginx render
- `opm enable <profiles>` — updates `.env`, regenerates nginx, starts profile containers
- `opm disable <profiles>` — stops containers, updates `.env`, reloads nginx
- `opm up / down` — start/stop all active profiles
- `opm status` — active profiles, RAM estimate, live service health table (via admin API, falls back to `docker ps`)
- `opm upgrade <profile>` — rolling update: pull + restart each service, waiting for health before continuing
- `opm nginx [--dry-run] [--reload]` — regenerate `infra/nginx/nginx.conf` from `nginx.conf.j2` template
- `opm config set/get/list` — read/write config via admin API
- `opm add-service <name>` — scaffold a new native FastAPI service (main.py, Dockerfile, requirements.txt, openhis.service.json)

### Profile Engine (`platform/profile_engine.py`)

- Reads `x-openhis` YAML metadata blocks from each profile compose file
- Functions: load profile metadata, resolve compose file list, dependency check, collect nginx routes, estimate RAM

### nginx Config Generator (`platform/nginx_gen.py`)

- Renders `infra/nginx/nginx.conf.j2` (Jinja2 template) based on active profiles
- Template uses `{% if 'emr' in active_profiles %}` gates for per-profile blocks
- Supports "extra upstreams/routes" loop for profiles not in the named block set
- Optionally runs `docker exec nginx nginx -s reload` after writing

### Service Registry (`services/admin` + DB)

- SQLite table `service_registry` (name, profile, internal_url, health_url, nginx_path, status, last_seen, metadata)
- Indexes on profile and status
- Base services auto-seeded on admin startup: admin, mpi, integration-hub, hl7
- Live health probing: concurrent `httpx` HEAD requests update status + last_seen
- REST CRUD: `GET /api/registry`, `GET /api/registry/{name}`, `POST /api/registry`, `DELETE /api/registry/{name}`

### Keycloak SSO (`infra/keycloak/openhis-realm.json`)

- Realm: `openhis`
- Roles: admin, clinician, radiologist, lab-tech, pharmacist, patient
- Confidential OIDC client: `openhis-platform` (RS256)
- Default admin user seeded with password `admin`
- Access token lifespan: 300 s
- Native services validate JWTs via JWKS endpoint (1-hour cache)
- Falls back to session token validation when Keycloak is not reachable (dev mode)

### Redis Event Bus (`redis:7-alpine`)

- Stream name: `openhis:events`
- Consumer groups created on startup: `mpi`, `analytics`, `hl7`
- AOF persistence — events survive container restarts
- Max stream length: 10 000 entries (approximate trim)
- Publisher (`services/integration-hub/app/bus.py`): `publish(event_type, payload)`, `ensure_stream()`, `close()`

---

## Admin Service (`services/admin`)

### Authentication & Sessions

- Username/password login with PBKDF2-SHA256 hashed passwords
- Bearer token sessions with configurable TTL (`SESSION_TTL_HOURS`)
- Logout immediately invalidates token
- JWT validation via Keycloak JWKS (RS256) when `KEYCLOAK_URL` is set
- Fallback to session token when Keycloak is unavailable

### User Management

- Roles: admin, superadmin
- Create / delete users (superadmin only)
- Password change with minimum length validation
- Cannot delete own account (403)

### System Configuration

- Key-value store for system-wide settings
- All changes recorded in audit log

### Service Health Monitor

- Live concurrent health probing of all registered services
- Status transitions: unknown → healthy / unhealthy
- Last-seen timestamp updated on every successful probe

### Audit Log

- Events: login, logout, user-created, password-changed, config-changed, registry-updated

### Announcements

- System-wide announcements with severity: info / warning / critical / success
- Active/inactive toggle

### Platform Topology (`GET /api/platform/topology`)

- Returns nodes (services) + edges (integration relationships) as a graph
- Node attributes: id, label, profile, status, nginx_path
- Edge attributes: source, target, label (e.g. "FHIR R4 poll", "publish events")

### Profile Management API

- `GET /api/profiles/active` — current profile list from `.env`
- `POST /api/profiles/enable` — append profiles to `.env`, regenerate nginx
- `POST /api/profiles/disable` — remove profiles from `.env`, regenerate nginx

### Live Event Stream (`GET /api/events/stream`)

- Server-Sent Events bridge over Redis Streams
- Reads forward from stream tail — admin UI sees only new events
- Keep-alive comment every 15 s (when no events arrive during BLOCK window)
- `?lastEventId=<id>` parameter to resume from a specific stream position
- `GET /api/events/recent?limit=100` — last N events as JSON for dashboard load

### Data Model

| Table | Key Fields |
|---|---|
| admin_users | id, username, password_hash, role, created_at, last_login |
| admin_sessions | id, user_id, token, expires_at |
| audit_log | id, actor, action, target, details, created_at |
| system_config | key, value, updated_at, updated_by |
| announcements | id, title, body, severity, active, created_at |
| service_registry | name, profile, internal_url, health_url, nginx_path, status, registered_at, last_seen, metadata |

---

## Master Patient Index (`services/mpi`)

### Patient Management

- Create, search, update master patients (MRN, name, DOB, sex, phone, address, insurance)
- Merge / deactivate duplicate master records

### Cross-Reference Registry

- Links patient identities across systems: OpenMRS, OpenELIS, legacy EHR
- Unique constraint: (system, system_id)
- Auto-populated via bus consumer when `patient.synced` events arrive from integration-hub
- REST CRUD: create, list, delete cross-references

### Probabilistic Patient Matching

- Fuzzy matching on name + DOB + sex
- Match candidates table with score, status (pending / confirmed / rejected), reviewer
- API: `POST /api/patients/match`

### Bus Consumer

- Consumer group: `mpi`
- Handles `patient.synced`: looks up master patient by MRN, upserts crossref entries for OpenMRS and OpenELIS system IDs

### Data Model

| Table | Key Fields |
|---|---|
| master_patients | id, mrn, firstname, lastname, birthdate, sex, phone, address, insurance_id, status, merged_into |
| cross_references | id, master_id, system, system_id, mrn, assigning_authority |
| match_candidates | id, master_id_a, master_id_b, score, status, reviewed_by |
| audit_log | id, master_id, action, performed_by, details |

---

## Integration Hub (`services/integration-hub`)

### Polling Sync Worker

- Interval: configurable (`POLL_INTERVAL_S`, default 60 s)
- **Patient sync**: OpenMRS FHIR R4 `Patient` → OpenELIS FHIR R4 `Patient`
- **Lab order routing**: OpenMRS `ServiceRequest` → OpenELIS `ServiceRequest`
- **Result routing**: OpenELIS `DiagnosticReport` → OpenMRS `DiagnosticReport`
- In-memory dedup sets reset on restart (all upserts are idempotent)

### Retry Queue

- Failed items queued with exponential back-off (`BASE_BACKOFF_S × 2^(attempt-1)`)
- Max 5 retry attempts; exhausted items recorded in audit log and increment error counter

### Event Publishing

- Publishes to `openhis:events` after each successful sync:
  - `patient.synced` — with `omrs_id`, `oe_id`, `mrn`
  - `lab_order.routed` — with `omrs_id`, `oe_id`
  - `lab_result.ready` — with `oe_id`, `subject`

### Audit Log

- SQLite (aiosqlite) with WAL mode
- Every event — success, failure, retry, exhaustion — written with resource type, ID, direction, status, detail

### REST API

- `GET /api/health` — status + counters (patients_synced, orders_synced, reports_synced, errors, last_poll_at)
- `GET /api/feed` — recent sync events
- `POST /api/poll` — trigger a manual poll cycle
- `GET /api/audit` — paginated audit log

### Adapters (`app/services/`)

- `openmrs.py` — FHIR R4 client for OpenMRS REST + FHIR endpoint
- `openelis.py` — FHIR R4 client for OpenELIS
- `odoo.py` — XML-RPC client for Odoo

---

## Analytics (`services/analytics`)

### Periodic Metrics Collection

- APScheduler-based (configurable interval, default 5 min)
- Collects snapshots from all active services via their health endpoints
- First collect runs 3 s after startup

### Bus Consumer

- Consumer group: `analytics`
- Receives all event types; records tallies in `event_counts` (date × event_type × source)
- New event types appear automatically in the dashboard without code changes
- `event_counts` table created on first startup if absent

### REST API

- `GET /api/metrics` — latest snapshot per service
- `GET /api/export` — CSV/JSON export of historical snapshots

### Data Model

| Table | Key Fields |
|---|---|
| snapshots | id, service, captured_at, payload (JSON) |
| event_counts | event_date, event_type, source, count |

---

## HL7 v2 Gateway (`services/hl7`)

### MLLP TCP Server

- Port 2575, asyncio-based, concurrent connections
- MLLP framing: SOB 0x0B / EOB 0x1C / CR 0x0D
- Returns AA (accepted) or AE (error) ACK

### Message Handling

| Message | Action |
|---|---|
| ADT^A01 | Admit → create/find patient in OpenMRS, create encounter |
| ADT^A02 | Transfer → update encounter ward/bed |
| ADT^A03 | Discharge → update encounter status |
| ADT^A04 | Register outpatient → sync to MPI |
| ADT^A08 | Update patient demographics |
| ADT^A40 | Merge patients via MPI |
| ORU^R01 | Lab result → forward to OpenMRS |

### REST API

- `POST /api/messages/inbound` — submit HL7 without MLLP
- `POST /api/messages/outbound/adt` / `oru` — build and send outbound messages
- `GET /api/messages` — full message log

---

## OpenMRS O3 (`emr` profile)

- **OpenMRS core**: Tomcat-based, MySQL 8, Liquibase migrations
- **OpenMRS O3 frontend**: React SPA (microfrontend architecture)
- **REST API**: `GET /openmrs/ws/rest/v1/`
- **FHIR R4**: `GET /openmrs/ws/fhir2/R4/` — Patient, Encounter, Observation, ServiceRequest, DiagnosticReport, MedicationRequest
- Patient registration, clinical encounters, orders, observations
- Integration with integration-hub via FHIR R4 polling

---

## OpenELIS Global 2 (`laboratory` profile)

- **OpenELIS**: Tomcat-based, PostgreSQL, Spring
- **REST API**: `GET /OpenELIS-Global/`
- **FHIR R4**: `GET /openelis-fhir/` — Patient, ServiceRequest, DiagnosticReport, Observation
- Lab order management, specimen tracking, result entry, QC
- Integration with integration-hub via FHIR R4 pull/push

---

## Odoo 17 Community (`erp` profile)

- **Odoo**: Python-based ERP, PostgreSQL
- **Web UI**: `http://localhost/odoo/` (React + OWL)
- **Modules**: Inventory, Pharmacy/dispensing, Billing, Procurement
- **XML-RPC API** used by integration-hub adapter
- Nginx handles subpath routing with `proxy_redirect` for root-relative Location headers

---

## Imaging Suite (`imaging` profile)

### Orthanc PACS

- DICOM C-STORE on port 4242
- PostgreSQL index backend
- Python plugin: notifies AI Controller on DICOM store, pushes AI results as secondary series

### OHIF Viewer

- DICOMweb viewer served at `/ohif/`
- Configured to point at Orthanc's DICOMweb endpoints

### RIS — Radiology Information System

- Imaging worklist, order management, report workflow (DRAFT → PRELIMINARY → FINAL → ADDENDUM)
- Auto-generated accession numbers

### AI Controller

- Pipeline registry: register Docker images as AI pipelines
- Auto-trigger rules: modality + body part → pipeline
- Spawns pipeline containers on demand; collects result.json; pushes findings back to Orthanc

### DICOM Simulator

- Generates synthetic DICOM studies: CR, DX, US, CT, MR, NM
- Body-part-aware pixel patterns
- Pushes directly to Orthanc; triggers AI pipeline if matching rule exists

---

## Legacy Services (archived in `services-legacy/`)

These services are **not started** by the default stack. They remain available for reference and can be started via `compose/profiles/legacy.yml`.

| Service | Replaced by |
|---|---|
| `ehr/` | OpenMRS O3 (`emr` profile) |
| `lis/` | OpenELIS Global 2 (`laboratory` profile) |
| `pharmacy/` | Odoo 17 (`erp` profile) |
| `fhir-bridge/` | Redis Streams event bus + integration-hub |

---

## Cross-Cutting Requirements

| Requirement | How Fulfilled |
|---|---|
| Profile-based selective deployment | `compose/base.yml` + `compose/profiles/*.yml`; `OPENHIS_PROFILES` env var |
| Platform management CLI | OPM (`platform/opm.py`) — init, enable, disable, status, upgrade, add-service |
| Centralized service registry | Admin DB `service_registry` table with live health probing |
| SSO / identity management | Keycloak 24 (RS256 JWKS); native services validate Bearer JWTs |
| Event-driven integration | Redis Streams (`openhis:events`); publisher in integration-hub; consumers in MPI, Analytics, HL7 |
| FHIR R4 interoperability | OpenMRS + OpenELIS native FHIR R4 APIs; integration-hub polls and pushes via FHIR |
| HL7 v2 interoperability | Full MLLP TCP server + ADT/ORU parser and builder |
| DICOM image storage | Orthanc PACS integration + multi-modality simulator |
| Master patient matching | MPI with crossref registry + probabilistic matching; auto-populated from bus events |
| Audit trail | Integration-hub audit log; admin audit log; MPI audit log |
| Dynamic nginx routing | `nginx.conf.j2` Jinja2 template; `nginx_gen.py` renders on profile change |
| Live platform visibility | Admin SSE event stream; service topology graph; RAM estimator |
| Containerization | All services on `openhis-net` Docker bridge; single nginx entry point |
| Scalability | Add profiles for more services; bus consumers scale independently |

---

## Service Port Map

| Service | Port | Profile |
|---|---|---|
| nginx | 80 | base |
| admin | 8011 | base |
| mpi | 8007 | base |
| integration-hub | 8012 | base |
| hl7 | 8009 (HTTP) / 2575 (MLLP) | base |
| keycloak | 8080 | base |
| redis | 6379 | base |
| postgres | 5432 | base |
| openmrs | 8080 (internal) | emr |
| openmrs-frontend | 80 (internal) | emr |
| openelis | 8080 (internal) | laboratory |
| odoo | 8069 (internal) | erp |
| orthanc | 8042 (HTTP) / 4242 (DICOM) | imaging |
| ohif | 80 (internal) | imaging |
| ris | 8002 | imaging |
| ai-controller | 8000 | imaging |
| simulator | 8001 | imaging |
| analytics | 8008 | analytics |
| patient-portal | 8010 | analytics |
