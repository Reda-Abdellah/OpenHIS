# PACS Demo Platform

A full clinical integration stack running entirely via Docker Compose — DICOM imaging, AI pipelines, EHR, LIS, RIS, Pharmacy, MPI, HL7 and FHIR R4 interoperability. All data is synthetic and intended for local development and demonstration only.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Quick Start](#quick-start)
4. [Service URLs](#service-urls)
5. [API Quick Reference](#api-quick-reference)
6. [Generating Sample Data](#generating-sample-data)
7. [AI Pipelines](#ai-pipelines)
8. [Optional: HAPI FHIR Server](#optional-hapi-fhir-server)
9. [Environment Variables](#environment-variables)
10. [Data Persistence](#data-persistence)
11. [Starting & Stopping](#starting--stopping)
12. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
Browser
   │
   ▼
┌─────────────────────────────────────────────────┐
│  nginx  (port 80)  — reverse proxy + portal     │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬───┘
   │      │      │      │      │      │      │
  EHR    RIS    LIS   AI-Ctrl  MPI   HL7  Pharmacy
         │             │
       Orthanc ◄── AI Runner (poc-xray / poc-ct)
         │
       OHIF Viewer
         │
    FHIR Bridge ──► HAPI FHIR Server (optional)
```

All services communicate **internally** over the `pacs-net` Docker bridge network using container names. The nginx reverse proxy is the **only** public entry point, listening on **port 80**.

### Services at a glance

| Service | Language / Framework | Database |
|---|---|---|
| portal | Static HTML | — |
| nginx | nginx 1.25 | — |
| admin | Python / FastAPI | SQLite |
| ehr | Python / FastAPI | SQLite |
| ris | Python / FastAPI | SQLite |
| lis | Python / FastAPI | SQLite |
| ai-controller | Python / FastAPI | SQLite |
| analytics | Python / FastAPI | SQLite |
| mpi | Python / FastAPI | SQLite |
| hl7 | Python / FastAPI | SQLite |
| pharmacy | Python / FastAPI | SQLite |
| patient-portal | Python / FastAPI | SQLite |
| fhir-bridge | Python / FastAPI | — (stateless router) |
| simulator | Python / FastAPI | in-memory |
| orthanc | Orthanc PACS | PostgreSQL |
| ohif | OHIF Viewer (nginx) | — |
| postgres | PostgreSQL 15 | — |

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
# 1. Clone and enter the repository
git clone <repo-url>
cd <repo-name>

# 2. Start the core stack
docker compose up -d

# Postgres initialises first — the init-databases.sh script runs automatically
# on a fresh volume and creates both the orthanc and hapifhir databases.

# 3. Wait ~20 s for all services to be healthy, then open the portal
open http://localhost
```

The portal at `http://localhost/` links to every module. No further configuration is required for a basic run.

---

## Service URLs

All services are accessible through nginx on **port 80**. Internal container-to-container calls bypass nginx entirely and use direct hostnames (e.g. `http://ehr:8003`).

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

Every FastAPI service exposes its API under `/<module>/api/` and its Swagger docs at `/<module>/docs`.

| Service | Health check | Swagger |
|---|---|---|
| EHR | `http://localhost/ehr/api/health` | `http://localhost/ehr/docs` |
| RIS | `http://localhost/ris/api/health` | `http://localhost/ris/docs` |
| LIS | `http://localhost/lis/api/health` | `http://localhost/lis/docs` |
| AI Controller | `http://localhost/ai-controller/api/health` | `http://localhost/ai-controller/docs` |
| Analytics | `http://localhost/analytics/api/health` | `http://localhost/analytics/docs` |
| MPI | `http://localhost/mpi/api/health` | `http://localhost/mpi/docs` |
| HL7 | `http://localhost/hl7/api/health` | `http://localhost/hl7/docs` |
| Pharmacy | `http://localhost/pharmacy/api/health` | `http://localhost/pharmacy/docs` |
| Patient Portal | `http://localhost/patient-portal/api/health` | `http://localhost/patient-portal/docs` |
| FHIR Bridge | `http://localhost/fhir-bridge/api/health` | `http://localhost/fhir-bridge/docs` |
| Admin | `http://localhost/admin/api/health` | `http://localhost/admin/docs` |
| Simulator | `http://localhost/simulator/api/health` | `http://localhost/simulator/docs` |
| Orthanc | `http://localhost/orthanc/system` | — |

### Special Ports (non-HTTP)

| Protocol | Port | Service | Purpose |
|---|---|---|---|
| DICOM C-STORE | `4242` | Orthanc | DICOM push from external tools |
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

All endpoints receive domain events POSTed by other services and translate them to FHIR R4.

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
3. Click **Generate & Push** — synthetic DICOM instances are built and pushed directly to Orthanc
4. The Orthanc plugin fires an event to the AI Controller, which queues an AI job automatically if a matching rule exists
5. Results appear in the **AI Controller** jobs view and as a secondary-capture series in **OHIF**

To generate data via the API directly:

```bash
curl -X POST http://localhost/simulator/api/generate \
  -H "Content-Type: application/json" \
  -d '{"modality": "CR", "patient": {"patientname": "DOE^JOHN", "patientid": "P001"}, "params": {"bodypart": "CHEST"}}'
```

---

## AI Pipelines

Two proof-of-concept pipelines are included. They run as isolated Docker containers spawned by the AI Controller.

| Pipeline ID | Image | Modalities | Output |
|---|---|---|---|
| `poc-xray` | `pipelines/poc-xray` | CR, DX | Findings JSON + overlay DICOM |
| `poc-ct` | `pipelines/poc-ct` | CT | Findings JSON + segmentation mask DICOM |

> **Note:** Findings are randomly generated for demonstration purposes only.

### Adding a new pipeline

1. Create a new directory under `pipelines/` with a `Dockerfile` and `run.py`
2. `run.py` must read `$JOBID` and use `$JOBSDATADIR/$JOBID/input/` for inputs and `$JOBSDATADIR/$JOBID/output/result.json` for outputs
3. Add the image build to `docker-compose.yml`
4. Register the pipeline via the AI Controller UI or API (`POST /ai-controller/api/pipelines`)
5. Add a trigger rule for the desired modality (`POST /ai-controller/api/rules`)

---

## Optional: HAPI FHIR Server

A HAPI FHIR R4 server is available as an optional profile. It requires the PostgreSQL `hapifhir` database, which is created automatically by `postgres/init-databases.sh`.

```bash
# Start with HAPI FHIR
docker compose --profile fhir up -d

# HAPI will auto-create its schema on first boot (~30 s)
# Access the FHIR UI at:
open http://localhost:8080/fhir
```

Enable the bridge to push resources to HAPI by setting these environment variables on the `fhir-bridge` service:

```yaml
FHIRSERVERURL: http://fhir-server:8080/fhir
FHIRENABLED: "true"
```

---

## Environment Variables

| Variable | Service(s) | Default | Description |
|---|---|---|---|
| `ROOTPATH` | all FastAPI services | (module name) | FastAPI `root_path` — must match the nginx location prefix |
| `DBPATH` | ehr, lis, ris, ai-controller, mpi, hl7, pharmacy, admin | `data/<svc>.db` | SQLite file path inside the container |
| `ORTHANCURL` | fhir-bridge, ai-controller, simulator, orthanc plugin | `http://orthanc:8042` | Orthanc REST base URL |
| `FHIRBRIDGEURL` | ehr, lis, ris, ai-controller, orthanc plugin, pharmacy | `http://fhir-bridge:8005/api` | FHIR Bridge event bus URL (must include `/api`) |
| `FHIRSERVERURL` | fhir-bridge | — | HAPI FHIR server URL; leave blank to disable push |
| `FHIRENABLED` | fhir-bridge | `true` | Set to `false` to disable FHIR push without stopping the bridge |
| `EHRURL` | fhir-bridge, pharmacy | `http://ehr:8003/api` | EHR internal API base URL |
| `RISURL` | fhir-bridge, ai-controller | `http://ris:8002/api` | RIS internal API base URL |
| `LISURL` | fhir-bridge | `http://lis:8004/api` | LIS internal API base URL |
| `PHARMACYURL` | fhir-bridge | `http://pharmacy:8006/api` | Pharmacy internal API base URL |
| `AICONTROLLERURL` | orthanc plugin | `http://ai-controller:8000` | AI Controller URL (no `/api`) |
| `JOBSDATADIR` | ai-controller, pipelines | `data/jobs` | Shared volume path for pipeline I/O artifacts |
| `HL7URL` | fhir-bridge | — | HL7 Gateway URL for ADT notifications |
| `DOCSDIR` | ehr | `data/documents` | File upload storage directory |

> ⚠️ `FHIRBRIDGEURL` must always end in `/api`. All services except `pharmacy` already have this correctly set — `pharmacy` needs to be updated from `http://fhir-bridge:8005` to `http://fhir-bridge:8005/api`.

---

## Data Persistence

All stateful data is stored in named Docker volumes. Containers can be restarted freely without data loss.

| Volume | Used by | Contents |
|---|---|---|
| `pg-data` | postgres | Orthanc index, HAPI FHIR resources |
| `orthanc-data` | orthanc | DICOM pixel data |
| `ris-data` | ris | RIS SQLite database |
| `ai-jobs` | ai-controller, pipelines | Pipeline job I/O artifacts |
| `ai-controller-db` | ai-controller | AI Controller SQLite database |
| `ehr-data` | ehr | EHR SQLite database + uploaded documents |
| `lis-data` | lis | LIS SQLite database |
| `pharmacydata` | pharmacy | Pharmacy SQLite database |
| `mpidata` | mpi | MPI SQLite database |
| `analytics-data` | analytics | Analytics SQLite database |
| `hl7-data` | hl7 | HL7 message store |
| `admin-data` | admin | Admin users, config, audit log |
| `portal-sessions` | patient-portal | Patient session tokens |

```bash
# Full reset — destroys ALL data
docker compose down -v
```

---

## Starting & Stopping

```bash
# Start everything
docker compose up -d

# Start with HAPI FHIR server
docker compose --profile fhir up -d

# Stop everything (data preserved in volumes)
docker compose down

# Stop and delete all data (full reset)
docker compose down -v

# Rebuild a single service after a code change
docker compose build ehr
docker compose up -d --no-deps ehr

# View logs
docker compose logs -f ehr
docker compose logs -f nginx
docker compose logs -f fhir-bridge
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `http://localhost` returns 404 or connection refused | nginx port not mapped to host | Ensure `docker-compose.yml` has `ports: - "80:80"` under the `nginx` service |
| Module UI loads but API calls return 404 | `BASE` constant in `static/index.html` missing leading `/` | Change `const BASE = "ehr"` to `const BASE = "/ehr"` in each module's `static/index.html` |
| nginx returns 502 for any module | Trailing slash missing on nginx location | Ensure `location /ehr/ { proxy_pass http://ehr/; }` — both must have trailing slashes |
| OHIF viewer shows blank or wrong routes | `routerBasename` not set | Add `routerBasename: '/ohif/'` to `ohif/app-config.js` |
| Pharmacy FHIR events silently fail | Missing `/api` suffix on `FHIRBRIDGEURL` | Set `FHIRBRIDGEURL: http://fhir-bridge:8005/api` in `docker-compose.yml` |
| `fhir-server` crash-loops on first start | `hapifhir` database missing | Run `docker compose exec postgres psql -U orthanc -c "CREATE DATABASE hapifhir;"` |
| AI findings never appear in FHIR | Missing FHIR callback in `runner.py` | Ensure `runner.py` posts to `FHIRBRIDGEURL/events/ai-job-completed` on job completion |
| Services start before upstreams are ready | `nginx` `depends_on` incomplete | Add all 14 services to the `depends_on` list of the `nginx` service |
| EHR or LIS crash on startup | Missing `static/` directory | Ensure `static/index.html` exists in each module directory |
| OpenAPI docs show wrong base path | `ROOTPATH` env var not set | Each FastAPI service must have `ROOTPATH=<module-name>` matching its nginx location prefix |
