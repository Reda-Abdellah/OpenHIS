# OpenHIS

A full hospital information system running entirely via Docker Compose — EHR, RIS, LIS, Pharmacy, MPI, Admin, Patient Portal, Analytics, HL7 v2, FHIR R4, DICOM/PACS, and AI imaging pipelines. All data is synthetic; intended for local development and demonstration only.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Repository Layout](#repository-layout)
3. [Prerequisites](#prerequisites)
4. [Quick Start](#quick-start)
5. [Service URLs](#service-urls)
6. [API Quick Reference](#api-quick-reference)
7. [Generating Sample Data](#generating-sample-data)
8. [AI Pipelines](#ai-pipelines)
9. [Optional: HAPI FHIR Server](#optional-hapi-fhir-server)
10. [Configuration](#configuration)
11. [Data Persistence](#data-persistence)
12. [Developer Commands](#developer-commands)
13. [Testing](#testing)
14. [Troubleshooting](#troubleshooting)

---

## Architecture

```
Browser
   │
   ▼
┌──────────────────────────────────────────────────────┐
│  nginx  (port 80)  — reverse proxy + portal          │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬─────────┘
   │      │      │      │      │      │      │
  EHR    RIS    LIS  AI-Ctrl  MPI   HL7  Pharmacy
         │             │
       Orthanc ◄── AI Runner (poc-xray / poc-ct)
         │
       OHIF Viewer
              \
          FHIR Bridge ──► HAPI FHIR Server (optional)
```

All services communicate **internally** over the `openhis-net` Docker bridge network using container names. The nginx reverse proxy is the **only** public entry point on **port 80**.

### Services at a glance

| Service | Framework | Database | Port (internal) |
|---|---|---|---|
| nginx | nginx 1.25 | — | 80 |
| admin | Python / FastAPI | SQLite | 8011 |
| ehr | Python / FastAPI | SQLite | 8003 |
| ris | Python / FastAPI | SQLite | 8002 |
| lis | Python / FastAPI | SQLite | 8004 |
| ai-controller | Python / FastAPI | SQLite | 8000 |
| analytics | Python / FastAPI | SQLite | 8008 |
| mpi | Python / FastAPI | SQLite | 8007 |
| hl7 | Python / FastAPI | SQLite | 8009 |
| pharmacy | Python / FastAPI | SQLite | 8006 |
| patient-portal | Python / FastAPI | SQLite | 8010 |
| fhir-bridge | Python / FastAPI | — (stateless) | 8005 |
| simulator | Python / FastAPI | in-memory | 8001 |
| orthanc | Orthanc PACS | PostgreSQL | 8042 |
| ohif | OHIF Viewer | — | 80 (internal) |
| postgres | PostgreSQL 15 | — | 5432 |

---

## Repository Layout

```
openhis/
├── services/          # 12 FastAPI microservices (one directory each)
│   ├── admin/
│   ├── ai-controller/
│   ├── analytics/
│   ├── ehr/
│   ├── fhir-bridge/
│   ├── hl7/
│   ├── lis/
│   ├── mpi/
│   ├── patient-portal/
│   ├── pharmacy/
│   ├── ris/
│   └── simulator/
├── pipelines/         # AI batch containers (spawned by ai-controller)
│   ├── poc-ct/
│   └── poc-xray/
├── infra/             # Third-party / infrastructure configuration
│   ├── fhir/          #   HAPI FHIR server (application.yaml)
│   ├── nginx/         #   Reverse proxy (nginx.conf)
│   ├── ohif/          #   DICOM viewer (app-config.js)
│   ├── orthanc/       #   PACS server + Python plugin
│   ├── portal/        #   Landing page (index.html)
│   └── postgres/      #   DB init scripts
├── tests/             # Pytest suites (one directory per service)
├── docker-compose.yml
├── .env.example       # Document all configurable environment variables
├── Makefile           # Developer shortcuts
└── README.md
```

Each service directory follows the same structure:

```
services/<name>/
├── Dockerfile
├── requirements.txt
├── main.py
├── database.py
├── routers/
└── static/index.html
```

---

## Prerequisites

| Tool | Minimum version |
|---|---|
| Docker Engine | 24.x |
| Docker Compose plugin | 2.20 |
| Available RAM | 4 GB |
| Available disk | 10 GB |

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd openhis

# 2. (Optional) copy and customise credentials
cp .env.example .env
# Edit .env — at minimum change POSTGRES_PASSWORD, ADMIN_PASS

# 3. Start the stack
make up
# or: docker compose up -d

# PostgreSQL initialises first; infra/postgres/init-databases.sh
# creates the orthanc and hapi_fhir databases automatically on a fresh volume.

# 4. Wait ~20 s for all services to become healthy, then open the portal
open http://localhost
```

The portal at `http://localhost/` links to every module. No further configuration is needed for a basic run.

---

## Service URLs

All services are accessed through nginx on **port 80**.

### Frontend UIs

| Service | URL |
|---|---|
| **Portal** | `http://localhost/` |
| **Admin Dashboard** | `http://localhost/admin/` |
| **EHR** | `http://localhost/ehr/` |
| **RIS** | `http://localhost/ris/` |
| **LIS** | `http://localhost/lis/` |
| **AI Controller** | `http://localhost/ai-controller/` |
| **Analytics** | `http://localhost/analytics/` |
| **MPI** | `http://localhost/mpi/` |
| **HL7 Gateway** | `http://localhost/hl7/` |
| **Pharmacy** | `http://localhost/pharmacy/` |
| **Patient Portal** | `http://localhost/patient-portal/` |
| **DICOM Simulator** | `http://localhost/simulator/` |
| **OHIF Viewer** | `http://localhost/ohif/` |
| **Orthanc PACS** | `http://localhost/orthanc/` |

### API & Health Endpoints

Every FastAPI service exposes its API under `/<service>/api/` and Swagger docs at `/<service>/docs`.

| Service | Health | Swagger |
|---|---|---|
| EHR | `/ehr/api/health` | `/ehr/docs` |
| RIS | `/ris/api/health` | `/ris/docs` |
| LIS | `/lis/api/health` | `/lis/docs` |
| AI Controller | `/ai-controller/api/health` | `/ai-controller/docs` |
| Analytics | `/analytics/api/health` | `/analytics/docs` |
| MPI | `/mpi/api/health` | `/mpi/docs` |
| HL7 | `/hl7/api/health` | `/hl7/docs` |
| Pharmacy | `/pharmacy/api/health` | `/pharmacy/docs` |
| Patient Portal | `/patient-portal/api/health` | `/patient-portal/docs` |
| FHIR Bridge | `/fhir-bridge/api/health` | `/fhir-bridge/docs` |
| Admin | `/admin/api/health` | `/admin/docs` |
| Simulator | `/simulator/api/health` | `/simulator/docs` |
| Orthanc | `/orthanc/system` | — |

### Special Ports (non-HTTP)

| Protocol | Port | Service | Purpose |
|---|---|---|---|
| DICOM C-STORE | `4242` | Orthanc | DICOM push from external modalities |
| MLLP | `2575` | HL7 Gateway | HL7 v2 message ingestion |

---

## API Quick Reference

### EHR — `/ehr/api`

| Method | Path | Description |
|---|---|---|
| GET / POST | `/patients` | List / register patients |
| GET / POST | `/encounters` | List / open encounters |
| GET / POST | `/orders` | Clinical orders (lab, imaging, pharmacy) |
| GET / POST | `/notes` | Clinical notes (draft → sign → amend) |
| POST | `/notes/{id}/sign` | Finalise a note → triggers FHIR Composition |
| GET / POST | `/documents` | Attach documents to notes |
| GET / POST | `/scheduling` | Appointments |
| GET / POST | `/billing` | Billing records |
| GET / POST | `/beds` | Bed management |
| GET | `/cdss/alerts` | Active CDSS alerts |

### RIS — `/ris/api`

| Method | Path | Description |
|---|---|---|
| GET / POST | `/patients` | Radiology patient registry |
| GET / POST | `/orders` | Imaging orders (worklist) |
| GET / POST | `/reports` | Radiology reports (DRAFT → PRELIMINARY → FINAL) |
| PUT | `/reports/{id}` | Update / finalise report → triggers FHIR DiagnosticReport |

### LIS — `/lis/api`

| Method | Path | Description |
|---|---|---|
| GET / POST | `/lab-patients` | Lab patient registry |
| GET / POST | `/specimens` | Specimen accessioning |
| POST | `/specimens/{id}/receive` | Mark specimen received |
| GET / POST | `/lab-orders` | Lab orders |
| GET / POST | `/results` | Submit results |
| PATCH | `/results/{id}/validate` | Validate result |
| GET / POST | `/qc` | Westgard QC rules |
| GET | `/instruments` | Analyser list |
| POST | `/instruments/run` | Simulate instrument run |

### AI Controller — `/ai-controller/api`

| Method | Path | Description |
|---|---|---|
| GET / POST | `/pipelines` | Register / list AI pipelines |
| GET / POST | `/rules` | Auto-trigger rules (modality, body part) |
| GET | `/jobs` | Job history |
| POST | `/jobs/{id}/trigger` | Manually trigger a pipeline |
| GET | `/artifacts` | Job output artifacts |
| POST | `/saveback` | Push AI result back to Orthanc |

### FHIR Bridge — `/fhir-bridge/api`

All endpoints receive domain events from other services and translate them to FHIR R4 resources.

| Event path | Source | FHIR resource |
|---|---|---|
| `/events/patient-created` | EHR | Patient |
| `/events/imaging-order` | EHR | ServiceRequest |
| `/events/lab-order` | EHR | ServiceRequest |
| `/events/pharmacy-order` | EHR | MedicationRequest |
| `/events/note-finalized` | EHR | Composition |
| `/events/lab-result-final` | LIS | DiagnosticReport |
| `/events/report-final` | RIS | DiagnosticReport |
| `/events/dicom-stored` | Orthanc plugin | ImagingStudy |
| `/events/ai-job-completed` | AI Controller | Observation |

---

## Generating Sample Data

1. Open the **DICOM Simulator** at `http://localhost/simulator/`
2. Select a modality (CR, DX, CT, MR, US, PT) and configure parameters
3. Click **Generate & Push** — synthetic DICOM instances are built in memory and pushed to Orthanc
4. The Orthanc plugin notifies the AI Controller, which queues a job automatically if a matching rule exists
5. Results appear in the AI Controller jobs view and as a secondary-capture series in OHIF

To generate data via the API:

```bash
curl -X POST http://localhost/simulator/api/generate \
  -H "Content-Type: application/json" \
  -d '{"modality":"CR","patient":{"patientname":"DOE^JOHN","patientid":"P001"},"params":{"bodypart":"CHEST"}}'
```

---

## AI Pipelines

Two proof-of-concept pipelines are included. They run as isolated Docker containers spawned on demand by the AI Controller.

| Pipeline ID | Image | Modalities | Output |
|---|---|---|---|
| `poc-xray` | `openhis/poc-xray:latest` | CR, DX | Findings JSON + overlay DICOM |
| `poc-ct` | `openhis/poc-ct:latest` | CT | Findings JSON + segmentation mask DICOM |

> **Note:** Findings are randomly generated for demonstration purposes only.

### Adding a new pipeline

1. Create a directory under `pipelines/` with a `Dockerfile` and `run.py`
2. `run.py` reads `$JOB_ID` and uses `$JOBS_DATA_DIR/$JOB_ID/input/` for inputs and `$JOBS_DATA_DIR/$JOB_ID/output/result.json` for outputs
3. Add an image build entry to `docker-compose.yml` (see `poc-xray-pipeline` as a template)
4. Register the pipeline via `POST /ai-controller/api/pipelines`
5. Add a trigger rule for the desired modality via `POST /ai-controller/api/rules`

---

## Optional: HAPI FHIR Server

A HAPI FHIR R4 server is available as an opt-in Docker Compose profile. The `hapi_fhir` database is created automatically by `infra/postgres/init-databases.sh` on first boot.

```bash
# Start with HAPI FHIR
make up-fhir
# or: docker compose --profile fhir up -d

# HAPI creates its schema on first start (~30 s)
open http://localhost:8080/fhir
```

The FHIR Bridge is already configured to push to HAPI when `FHIR_SERVER_URL` is set in the environment. See `.env.example` for details.

---

## Configuration

Copy `.env.example` to `.env` and adjust before first start. Docker Compose automatically loads `.env` from the project root.

| Variable | Service(s) | Default | Description |
|---|---|---|---|
| `POSTGRES_PASSWORD` | postgres, orthanc | `orthanc` | PostgreSQL password |
| `ADMIN_USER` | admin | `admin` | Admin dashboard username |
| `ADMIN_PASS` | admin | `admin123` | Admin dashboard password |
| `DB_PATH` | all SQLite services | `/data/<svc>.db` | SQLite file path inside the container |
| `ROOT_PATH` | all FastAPI services | `/<service>` | FastAPI `root_path`; must match the nginx location prefix |
| `FHIR_BRIDGE_URL` | ehr, lis, ris, ai-controller, orthanc plugin | `http://fhir-bridge:8005` | FHIR Bridge base URL (services append `/api/events/…` themselves) |
| `FHIR_SERVER_URL` | fhir-bridge | `http://fhir-server:8080/fhir` | HAPI FHIR server URL; leave blank to disable push |
| `FHIR_ENABLED` | fhir-bridge | `true` | Set `false` to disable FHIR push without stopping the bridge |
| `ORTHANC_URL` | ai-controller, simulator, ris | `http://orthanc:8042` | Orthanc REST base URL |
| `JOBS_DATA_DIR` | ai-controller, pipelines | `/data/jobs` | Shared volume path for pipeline I/O |
| `JOBS_VOLUME_NAME` | ai-controller | `openhis_ai-jobs` | Docker volume name for job artifacts |
| `DOCKER_NETWORK` | ai-controller | `openhis_openhis-net` | Docker network for spawned pipeline containers |
| `SESSION_TTL_HOURS` | admin, patient-portal | `12` / `24` | Session expiry in hours |
| `COLLECT_INTERVAL_MIN` | analytics | `5` | Metrics collection interval (minutes) |
| `MLLP_PORT` | hl7 | `2575` | MLLP listener port |

---

## Data Persistence

All stateful data lives in named Docker volumes. Containers can be restarted freely without data loss.

| Volume | Used by | Contents |
|---|---|---|
| `pg-data` | postgres | Orthanc index, HAPI FHIR resources |
| `orthanc-data` | orthanc | DICOM pixel data |
| `ehr-data` | ehr | EHR SQLite database + uploaded documents |
| `ris-data` | ris | RIS SQLite database |
| `lis-data` | lis | LIS SQLite database |
| `pharmacy_data` | pharmacy | Pharmacy SQLite database |
| `mpi_data` | mpi | MPI SQLite database |
| `analytics-data` | analytics | Analytics SQLite database |
| `hl7-data` | hl7 | HL7 message store |
| `ai-jobs` | ai-controller, pipelines | Pipeline job I/O artifacts |
| `ai-controller-db` | ai-controller | AI Controller SQLite database |
| `admin-data` | admin | Users, config, audit log |
| `portal-sessions` | patient-portal | Patient session tokens |

```bash
# Full reset — destroys ALL data
make clean
# or: docker compose down -v
```

---

## Developer Commands

A `Makefile` at the project root provides common shortcuts:

```bash
make up            # Start all services (detached)
make down          # Stop all services (data preserved)
make build         # Rebuild all images
make up-build      # Rebuild and start

make logs          # Tail logs for all services
make logs-service SVC=ehr   # Tail logs for one service

make restart SVC=ehr        # Restart a single service
make ps            # Show service status

make up-fhir       # Start with optional HAPI FHIR profile
make clean         # Stop and remove all volumes (destructive)
```

To rebuild and restart a single service after a code change:

```bash
docker compose build ehr
docker compose up -d --no-deps ehr
```

---

## Testing

Tests live in `tests/` with one sub-directory per service:

```bash
# Run the full suite
make test
# or: python -m pytest tests/ -v

# Run tests for a single service
make test-service SVC=ehr
# or: python -m pytest tests/ehr/ -v
```

All tests use a fresh in-memory or `/tmp` SQLite database per test and disable outbound HTTP calls (FHIR Bridge URL is set to empty string in fixtures). No running Docker containers are required to run the tests.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `http://localhost` returns 404 or connection refused | nginx port not mapped | Ensure `ports: ["80:80"]` is present under the `nginx` service in `docker-compose.yml` |
| Module UI loads but API calls return 404 | `BASE` constant missing leading `/` | Change `const BASE = "ehr"` to `const BASE = "/ehr"` in the module's `static/index.html` |
| nginx returns 502 for any module | Trailing slash missing on nginx location | Both the location and `proxy_pass` must have a trailing slash: `location /ehr/ { proxy_pass http://ehr/; }` |
| OHIF viewer shows blank or wrong routes | `routerBasename` not set | Add `routerBasename: '/ohif/'` to `infra/ohif/app-config.js` |
| `fhir-server` crash-loops on first start | `hapi_fhir` database missing | Run `docker compose exec postgres psql -U orthanc -c "CREATE DATABASE hapi_fhir;"` |
| AI findings never appear in FHIR | FHIR Bridge URL misconfigured | `FHIR_BRIDGE_URL` must be the base URL only (e.g. `http://fhir-bridge:8005`) — each service appends `/api/events/…` itself |
| OpenAPI docs show wrong base path | `ROOT_PATH` not set | Each FastAPI service must have `ROOT_PATH=/<service>` matching its nginx location prefix |
| EHR or LIS crash on startup | Missing `static/` directory | Ensure `services/<name>/static/index.html` exists |
| Pipeline containers fail to start | Wrong volume or network name | Verify `JOBS_VOLUME_NAME` and `DOCKER_NETWORK` match the Docker Compose project name (`openhis_ai-jobs`, `openhis_openhis-net`) |
