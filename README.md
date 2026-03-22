# PACS / Clinical Integration Platform

A fully containerised, locally-runnable stack that wires together medical imaging
(PACS), radiology workflows (RIS), electronic health records (EHR), laboratory
information (LIS), AI-assisted diagnostics, and a FHIR R4 interoperability layer.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Services & URLs](#services--urls)
3. [API Quick Reference](#api-quick-reference)
4. [Fresh Install — Step by Step](#fresh-install--step-by-step)
5. [Starting / Stopping](#starting--stopping)
6. [Optional: HAPI FHIR Server](#optional-hapi-fhir-server)
7. [Generating Sample Data](#generating-sample-data)
8. [Environment Variables Reference](#environment-variables-reference)
9. [Data Persistence](#data-persistence)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          NGINX  (port 80)                               │
│   /          /orthanc/   /ris/   /ai-controller/   /simulator/          │
│   /ehr/      /lis/       /fhir-bridge/   /fhir/                         │
└────┬──────────┬──────────┬────────┬────────┬─────────┬────────┬─────────┘
     │          │          │        │        │         │        │
  ┌──▼──┐  ┌───▼───┐  ┌───▼──┐ ┌──▼────┐ ┌─▼──────┐ ┌▼──┐ ┌──▼──────────┐
  │OHIF │  │Orthanc│  │ RIS  │ │  AI   │ │Simulat.│ │EHR│ │     LIS     │
  │View │  │ PACS  │  │:8002 │ │ Ctrl  │ │  :8001 │ │:80│ │    :8004    │
  └─────┘  │ :8042 │  └──┬───┘ │ :8000 │ └────────┘ │03 │ └──────┬──────┘
           └───┬───┘     │     └──┬────┘             └─┬─┘        │
               │         │        │                    │          │
               │    ┌────▼────────▼────────────────────▼──────────▼───┐
               │    │            FHIR Bridge  :8005                    │
               │    │  patient-created · imaging-order · lab-order     │
               │    │  lab-result-final · report-final · dicom-stored  │
               │    │  ai-job-completed                                │
               │    └────────────────────────┬─────────────────────────┘
               │                             │ (opt-in --profile fhir)
               │                             ▼
               │                    ┌─────────────────┐
               │                    │  HAPI FHIR R4   │
               │                    │   Server :8080  │
               │                    └────────┬────────┘
               │                             │
               └──────────────────┐          │
                                  ▼          ▼
                           ┌─────────────────────┐
                           │     PostgreSQL       │
                           │  db: orthanc         │
                           │  db: hapi_fhir       │
                           └─────────────────────┘

  EHR/LIS/AI-Controller use SQLite (file-based, mounted volumes)
```

### Event Flow

```
New Patient (EHR)
  └─► FHIR Bridge ──► FHIR Patient resource (HAPI)
                  ──► RIS  POST /patients/from-ehr
                  ──► LIS  POST /lab-patients

DICOM stored (Orthanc plugin)
  ├─► AI Controller  ──► runs pipeline ──► FHIR Bridge ──► FHIR Observations
  └─► FHIR Bridge    ──► FHIR ImagingStudy (HAPI)

LAB order (EHR)
  └─► FHIR Bridge ──► FHIR ServiceRequest
                  ──► LIS specimen + order created
                  ──► EHR order updated with LIS ref (PATCH)

LAB result finalised (LIS)
  └─► FHIR Bridge ──► FHIR DiagnosticReport (lab)
                  ──► EHR CDSS alert evaluation

IMAGING order (EHR)
  └─► FHIR Bridge ──► FHIR ServiceRequest
                  ──► RIS order created
                  ──► EHR order updated with accession (PATCH)

Radiology report FINAL (RIS)
  └─► FHIR Bridge ──► FHIR DiagnosticReport (radiology)
```

---

## Services & URLs

All services are accessed through nginx on **http://localhost**.

| Service | URL | API Docs | Port (internal) | Notes |
|---|---|---|---|---|
| OHIF Viewer | http://localhost/ | — | 80 | DICOM web viewer |
| Orthanc PACS | http://localhost/orthanc/ | — | 8042 | Login: `orthanc / orthanc` |
| RIS | http://localhost/ris/ | http://localhost/ris/docs | 8002 | Radiology IS |
| AI Controller | http://localhost/ai-controller/ | http://localhost/ai-controller/docs | 8000 | Pipeline orchestrator |
| Simulator | http://localhost/simulator/ | — | 8001 | DICOM test data generator |
| EHR | http://localhost/ehr/ | http://localhost/ehr/docs | 8003 | Electronic Health Records |
| LIS | http://localhost/lis/ | http://localhost/lis/docs | 8004 | Laboratory IS |
| FHIR Bridge | http://localhost/fhir-bridge/ | http://localhost/fhir-bridge/docs | 8005 | Internal event bus |
| HAPI FHIR R4 | http://localhost/fhir/ | http://localhost/fhir/swagger-ui/ | 8080 | Optional (`--profile fhir`) |

### Health endpoints

```
GET http://localhost/ehr/api/health
GET http://localhost/lis/api/health
GET http://localhost/fhir-bridge/api/health
GET http://localhost/ris/api/health
GET http://localhost/ai-controller/api/health
```

---

## API Quick Reference

### EHR  (`/ehr/api/`)

| Method | Path | Description |
|---|---|---|
| GET/POST | `/patients` | List / create patients |
| GET/PATCH/DELETE | `/patients/{id}` | Get / update / delete patient |
| GET/POST | `/patients/{id}/allergies` | Manage allergies |
| GET/POST | `/patients/{id}/diagnoses` | Manage diagnoses |
| GET/POST | `/encounters` | List / admit patient |
| PATCH | `/encounters/{id}` | Update / discharge encounter |
| GET/POST | `/orders` | List / create clinical orders (LAB/IMAGING/PHARMACY) |
| PATCH | `/orders/{id}` | Update order status / external ref |
| POST | `/orders/from-lis-result` | Receive LIS result → CDSS (called by FHIR bridge) |
| GET | `/cdss/alerts` | List CDSS alerts |
| POST | `/cdss/alerts/{id}/acknowledge` | Acknowledge alert |
| GET/POST | `/appointments` | Scheduling |
| GET/POST | `/billing` | Billing records |

### LIS  (`/lis/api/`)

| Method | Path | Description |
|---|---|---|
| GET/POST | `/lab-patients` | Upsert lab patient registry |
| GET/POST | `/specimens` | Accession specimens |
| POST | `/specimens/{id}/receive` | Mark specimen received |
| GET/POST | `/lab-orders` | Create lab orders |
| GET | `/lab-orders/catalog` | Available test codes |
| GET/POST | `/results` | Submit results |
| PATCH | `/results/{id}/validate` | Validate result |
| GET/POST | `/qc` | QC record entry / Westgard evaluation |
| GET | `/instruments` | List analysers |
| POST | `/instruments/run` | Simulate instrument run |

### FHIR Bridge events  (`/fhir-bridge/api/events/`)

| Method | Path | Called by |
|---|---|---|
| POST | `/patient-created` | EHR |
| POST | `/imaging-order` | EHR |
| POST | `/lab-order` | EHR |
| POST | `/lab-result-final` | LIS |
| POST | `/report-final` | RIS |
| POST | `/dicom-stored` | Orthanc plugin |
| POST | `/ai-job-completed` | AI Controller |

---

## Fresh Install — Step by Step

### Prerequisites

| Tool | Minimum version |
|---|---|
| Docker Engine | 24.x |
| Docker Compose plugin | 2.20 |
| Available RAM | 4 GB |
| Available disk | 10 GB |

### 1 — Clone and enter the repository

```bash
git clone <repo-url>
cd <repo-name>
```

### 2 — Apply fixes (if starting from the audit patches)

```bash
# Drop in the ZIP contents
unzip fixes.zip -d .

# Apply the 3 manual patches described in PATCHES.md
# (runner.py, ris/routers/reports.py, docker-compose.yml)
```

### 3 — Build all images

```bash
docker compose build
```

This builds: `orthanc`, `ris`, `ai-controller`, `simulator`, `ehr`, `lis`,
`fhir-bridge`, and the two POC pipeline images (`poc-xray`, `poc-ct`).
First build takes ~5–10 min depending on internet speed.

### 4 — Start the core stack

```bash
docker compose up -d
```

Postgres initialises first (the `init-databases.sh` script runs automatically
on a fresh volume and creates both the `orthanc` and `hapi_fhir` databases).

### 5 — Wait for all services to become healthy

```bash
watch docker compose ps
```

All services should show `healthy` within ~60 seconds. If a service restarts
once, that is normal during cold start while Postgres is still initialising.

### 6 — Verify

```bash
curl -s http://localhost/ehr/api/health   | python3 -m json.tool
curl -s http://localhost/lis/api/health   | python3 -m json.tool
curl -s http://localhost/fhir-bridge/api/health | python3 -m json.tool
```

Expected response shape:
```json
{ "status": "ok", "service": "ehr", "version": "1.0.0", "patients": 0 }
```

### 7 — (Optional) Start HAPI FHIR Server

```bash
docker compose --profile fhir up -d fhir-server
```

HAPI will auto-create the `hapi_fhir` schema in Postgres on first boot (~30 s).
Access the FHIR UI at **http://localhost/fhir/**.

---

## Starting / Stopping

```bash
# Start everything
docker compose up -d

# Start with HAPI FHIR
docker compose --profile fhir up -d

# Stop everything (data is preserved in volumes)
docker compose down

# Stop and DELETE all data (full reset)
docker compose down -v

# Rebuild a single service after a code change
docker compose build ehr
docker compose up -d --no-deps ehr

# View logs
docker compose logs -f ehr
docker compose logs -f fhir-bridge
```

---

## Generating Sample Data

### DICOM studies (via Simulator)

Open **http://localhost/simulator/** and use the web UI, or:

```bash
# CLI: generate 5 chest X-ray studies
python3 scripts/generate_sample.py --modality CR --count 5
```

Studies are sent directly to Orthanc. Auto-trigger rules in the AI Controller
will pick them up immediately if enabled.

### EHR patients + orders (via API)

```bash
# Create a patient
curl -s -X POST http://localhost/ehr/api/patients   -H "Content-Type: application/json"   -d '{"mrn":"MRN-001","first_name":"Jane","last_name":"Doe",
       "birth_date":"1985-03-15","sex":"F","phone":"555-0100"}'   | python3 -m json.tool

# Create a LAB order for that patient (use the id returned above)
curl -s -X POST http://localhost/ehr/api/orders   -H "Content-Type: application/json"   -d '{"order_type":"LAB","patient_id":"<id>",
       "order_detail":{"test_code":"CBC","specimen_type":"blood"},
       "priority":"STAT","requesting_physician":"Dr Smith"}'   | python3 -m json.tool
```

The FHIR Bridge will automatically propagate the patient to RIS and LIS,
and the order to LIS (specimen + lab order created).

---

## Environment Variables Reference

| Variable | Service | Default | Description |
|---|---|---|---|
| `FHIR_BRIDGE_URL` | ehr, lis, ris, ai-controller, orthanc | `""` | Set to `http://fhir-bridge:8005` to enable FHIR events |
| `FHIR_SERVER_URL` | fhir-bridge | `""` | Set to `http://fhir-server:8080/fhir` to push resources |
| `FHIR_ENABLED` | fhir-bridge | `true` | Set to `false` to disable FHIR push without stopping the bridge |
| `DBPATH` | ehr, lis | `data/*.db` | SQLite file path inside the container |
| `ROOT_PATH` | ehr, lis, ris, ai-controller | `""` | FastAPI root path for reverse-proxy prefix stripping |
| `AI_CONTROLLER_URL` | orthanc plugin | `http://ai-controller:8000` | AI pipeline trigger target |
| `JOBS_DATA_DIR` | ai-controller | `data/jobs` | Directory for pipeline I/O artifacts |
| `ORTHANC_URL` | fhir-bridge, ai-controller | `http://orthanc:8042` | Orthanc REST base URL |

---

## Data Persistence

| Volume | Used by | Contents |
|---|---|---|
| `postgres-data` | postgres | Orthanc index, HAPI FHIR resources |
| `orthanc-data` | orthanc | DICOM pixel data |
| `ris-data` | ris | RIS SQLite database |
| `ai-controller-data` | ai-controller | SQLite DB + pipeline job artifacts |
| `ehr-data` | ehr | EHR SQLite database |
| `lis-data` | lis | LIS SQLite database |

Run `docker compose down -v` to wipe all volumes and start completely fresh.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ehr` / `lis` crash on startup | Missing `static/` directory | Drop in `ehr/static/index.html` and `lis/static/index.html` from the fixes ZIP |
| nginx returns 502 for `/ehr/` or `/lis/` | Upstreams not defined or wrong port | Replace `nginx/nginx.conf` from the fixes ZIP and `docker compose restart nginx` |
| FHIR bridge calls return 405 | `_post` used instead of `_patch` for EHR order updates | Replace `fhir-bridge/routers/events.py` from the fixes ZIP |
| `fhir-server` crash-loops | `hapi_fhir` database missing | Run: `docker compose exec postgres psql -U orthanc -c "CREATE DATABASE hapi_fhir;"` |
| RIS returns 404 for `/api/patients/from-ehr` | `from-ehr` endpoint not added | Replace `ris/routers/patients.py` from the fixes ZIP |
| AI findings never appear in FHIR | `runner.py` missing FHIR callback | Apply runner.py patch from `PATCHES.md` and rebuild: `docker compose build ai-controller` |
