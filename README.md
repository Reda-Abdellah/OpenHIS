# OpenHIS

A profile-driven Health Information Platform running entirely via Docker Compose. Deploy everything (EMR, Lab, Imaging, ERP, Analytics) or just the modules you need — from a small clinic to a full hospital.

All data is synthetic; intended for local development and demonstration.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Repository Layout](#repository-layout)
3. [Prerequisites](#prerequisites)
4. [Quick Start](#quick-start)
5. [Profiles](#profiles)
6. [OPM — Platform Manager](#opm--platform-manager)
7. [Service URLs](#service-urls)
8. [API Quick Reference](#api-quick-reference)
9. [Event Bus](#event-bus)
10. [Generating Sample Data](#generating-sample-data)
11. [AI Pipelines](#ai-pipelines)
12. [Configuration](#configuration)
13. [Data Persistence](#data-persistence)
14. [Developer Commands](#developer-commands)
15. [Testing](#testing)
16. [Troubleshooting](#troubleshooting)

---

## Architecture

```
Browser
   │
   ▼
┌──────────────────────────────────────────────────────────────────┐
│  nginx  (port 80)  — single entry point, profile-aware routing   │
└─┬─────┬──────┬──────┬──────┬──────┬────────┬──────┬─────────────┘
  │     │      │      │      │      │        │      │
Admin  MPI  Keycloak Hub   HL7  OpenMRS OpenELIS  Odoo
  │     │      │      │              │      │
  │     └──────┘   Redis ◄───────── │ ─────┘
  │   crossref   (events stream)    │
  │                                 │
Analytics ◄──────────────────────── ┘
  (bus consumer: tallies all events)

Imaging profile:
  nginx → OHIF → Orthanc ← AI-Controller ← poc-xray / poc-ct pipelines
                    │
                   RIS
```

All services communicate over the **`openhis-net`** Docker bridge network. nginx on **port 80** is the only public entry point. The Redis stream (`openhis:events`) is the integration backbone — every cross-service event flows through it.

### Base stack (always on)

| Service | Image / Framework | Port | Role |
|---|---|---|---|
| nginx | nginx:1.25-alpine | 80 | Reverse proxy + portal |
| admin | FastAPI / SQLite | 8011 | Management plane |
| mpi | FastAPI / SQLite | 8007 | Master Patient Index |
| integration-hub | FastAPI / SQLite | 8012 | Polling sync + event publisher |
| hl7 | FastAPI / SQLite | 8009 | HL7 v2 MLLP gateway |
| keycloak | Keycloak 24 | 8080 | SSO / JWKS identity provider |
| redis | Redis 7 | 6379 | Event bus (Streams) |
| postgres | PostgreSQL 15 | 5432 | Shared relational DB |

### Profile services (opt-in)

| Profile | Services | Key technologies |
|---|---|---|
| `emr` | OpenMRS O3, openmrs-frontend | Tomcat, MySQL, React |
| `laboratory` | OpenELIS Global 2 | Tomcat, PostgreSQL |
| `erp` | Odoo 17 Community | Python, PostgreSQL |
| `imaging` | Orthanc, OHIF, RIS, AI Controller, Simulator | DICOM, DICOMweb |
| `analytics` | Analytics, Patient Portal | FastAPI, SQLite |

---

## Repository Layout

```
OpenHIS/
├── compose/
│   ├── base.yml               # Always-on services
│   ├── docker-compose.yml     # Full-stack orchestrator (include: all profiles)
│   └── profiles/
│       ├── emr.yml            # OpenMRS O3
│       ├── laboratory.yml     # OpenELIS Global 2
│       ├── erp.yml            # Odoo 17
│       ├── imaging.yml        # Orthanc + OHIF + RIS + AI
│       └── analytics.yml      # Analytics + Patient Portal
├── services/                  # Native FastAPI microservices
│   ├── admin/                 #   Management plane
│   ├── mpi/                   #   Master Patient Index
│   ├── integration-hub/       #   Polling sync + event publisher
│   ├── hl7/                   #   HL7 v2 gateway
│   ├── analytics/             #   Metrics collector + dashboard
│   ├── patient-portal/        #   Patient-facing portal
│   ├── ris/                   #   Radiology Information System
│   ├── ai-controller/         #   AI pipeline orchestrator
│   └── simulator/             #   DICOM modality simulator
├── services-legacy/           # Archived (not started by default)
│   ├── ehr/                   #   Legacy EHR (replaced by OpenMRS)
│   ├── lis/                   #   Legacy LIS (replaced by OpenELIS)
│   ├── pharmacy/              #   Legacy pharmacy (replaced by Odoo)
│   └── fhir-bridge/          #   Legacy event router (replaced by Redis bus)
├── platform/
│   ├── opm.py                 # OPM CLI (platform manager)
│   ├── profile_engine.py      # Profile metadata reader
│   └── nginx_gen.py           # nginx config renderer
├── infra/
│   ├── keycloak/              # Realm JSON seed
│   ├── nginx/                 # nginx.conf + nginx.conf.j2 template
│   ├── redis/                 # redis.conf
│   ├── ohif/                  # OHIF app-config.js
│   ├── orthanc/               # Orthanc config + Python plugin
│   └── portal/                # Landing page
├── pipelines/
│   ├── poc-xray/              # X-ray AI pipeline container
│   └── poc-ct/                # CT AI pipeline container
├── docs/                      # Contract specifications
│   ├── service-contract.md
│   ├── adapter-contract.md
│   └── profile-contract.md
├── tests/                     # Pytest suites
├── documentation/             # Planning documents
├── Makefile
├── .env.example
└── README.md
```

---

## Prerequisites

| Tool | Minimum version |
|---|---|
| Docker Engine | 24.x |
| Docker Compose plugin | 2.20 |
| Python (for OPM CLI) | 3.11 |
| Available RAM | 4 GB (base only) / 8 GB (emr + lab) / 16 GB (full stack) |
| Available disk | 15 GB |

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd OpenHIS

# 2. Run the first-time wizard (sets passwords, chooses profiles, writes .env)
python platform/opm.py init

# 3. Start the base stack + chosen profiles
make up
# or: python platform/opm.py up

# 4. Open the portal
open http://localhost
```

### Minimal start (base only — no EMR/Lab/ERP)

```bash
OPENHIS_PROFILES="" make up
# or: docker compose -f compose/base.yml up -d
```

---

## Profiles

Profiles are Docker Compose overlays. Each adds a cohesive set of services. Unselected profiles consume zero resources.

| Profile | What it adds | RAM estimate |
|---|---|---|
| `emr` | OpenMRS O3 (EMR + FHIR R4 API) | +2 GB |
| `laboratory` | OpenELIS Global 2 (LIS + FHIR R4 API) | +1 GB |
| `erp` | Odoo 17 (pharmacy, procurement, billing) | +1 GB |
| `imaging` | Orthanc PACS + OHIF viewer + RIS + AI | +1.5 GB |
| `analytics` | Analytics dashboard + Patient Portal | +256 MB |

### Selecting profiles

**In `.env`** (persists across restarts):
```bash
OPENHIS_PROFILES=emr,laboratory,imaging
```

**Via OPM:**
```bash
python platform/opm.py enable emr laboratory
python platform/opm.py disable erp
```

**Via Makefile:**
```bash
make emr-up
make lab-up
make imaging-up
OPENHIS_PROFILES=emr,laboratory make up
```

---

## OPM — Platform Manager

`platform/opm.py` is the OpenHIS Platform Manager CLI. Install its dependencies once:

```bash
pip install -r platform/requirements.txt
```

### Commands

```bash
# First-time setup wizard
python platform/opm.py init

# Enable profiles (updates .env, regenerates nginx, starts containers)
python platform/opm.py enable emr laboratory

# Disable profiles (stops containers, updates .env, reloads nginx)
python platform/opm.py disable erp

# Show active profiles + live service health (calls admin API)
python platform/opm.py status

# Start all active profiles
python platform/opm.py up

# Stop all containers (keeps volumes)
python platform/opm.py down

# Rolling upgrade (pull + restart one service at a time)
python platform/opm.py upgrade emr

# Regenerate nginx.conf from active profiles
python platform/opm.py nginx --reload

# Scaffold a new native FastAPI service
python platform/opm.py add-service my-service --port 8020 --profile base

# Config (via admin API)
python platform/opm.py config set admin maintenance_mode true
python platform/opm.py config get maintenance_mode
```

---

## Service URLs

All services are accessed through nginx on **port 80**.

### Always-on (base stack)

| Service | URL | Default credentials |
|---|---|---|
| **Portal** | `http://localhost/` | — |
| **Admin Dashboard** | `http://localhost/admin/` | admin / admin123 |
| **Admin API** | `http://localhost/admin/docs` | — |
| **MPI** | `http://localhost/mpi/` | — |
| **Keycloak** | `http://localhost/keycloak/` | admin / admin |
| **Integration Hub** | `http://localhost/integration-hub/docs` | — |
| **HL7 Gateway** | `http://localhost/hl7/` | — |

### EMR profile

| Service | URL | Default credentials |
|---|---|---|
| **OpenMRS O3** (SPA) | `http://localhost/openmrs/spa/` | admin / Admin123 |
| **OpenMRS REST** | `http://localhost/openmrs/ws/rest/v1/` | — |
| **OpenMRS FHIR** | `http://localhost/openmrs/ws/fhir2/R4/` | — |

### Laboratory profile

| Service | URL | Default credentials |
|---|---|---|
| **OpenELIS** | `http://localhost/OpenELIS-Global/` | admin / adminADMIN! |
| **OpenELIS FHIR** | `http://localhost/openelis-fhir/` | — |

### ERP profile

| Service | URL | Default credentials |
|---|---|---|
| **Odoo** | `http://localhost/odoo/` | admin / admin |

### Imaging profile

| Service | URL |
|---|---|
| **OHIF Viewer** | `http://localhost/ohif/` |
| **Orthanc PACS** | `http://localhost/orthanc/` |
| **RIS** | `http://localhost/ris/` |
| **AI Controller** | `http://localhost/ai-controller/` |
| **DICOM Simulator** | `http://localhost/simulator/` |

### Analytics profile

| Service | URL |
|---|---|
| **Analytics** | `http://localhost/analytics/` |
| **Patient Portal** | `http://localhost/patient-portal/` |

### Special ports

| Protocol | Port | Service |
|---|---|---|
| DICOM C-STORE | `4242` | Orthanc — push DICOM from external modalities |
| MLLP | `2575` | HL7 Gateway — HL7 v2 message ingestion |

---

## API Quick Reference

### Admin — `/admin/api`

| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | Obtain session token |
| GET | `/users` | List users |
| GET | `/registry` | Service registry (live health) |
| GET | `/platform/topology` | Service dependency graph |
| GET | `/platform/profiles` | All profiles with RAM estimate |
| GET | `/profiles/active` | Currently active profiles |
| POST | `/profiles/enable` | Enable profiles |
| POST | `/profiles/disable` | Disable profiles |
| GET | `/events/stream` | SSE live event feed (EventSource) |
| GET | `/events/recent` | Last 100 events as JSON |
| GET | `/config` | System configuration |
| PUT | `/config/{key}` | Update configuration |
| GET | `/announcements` | Active announcements |

### MPI — `/mpi/api`

| Method | Path | Description |
|---|---|---|
| GET / POST | `/patients` | List / register master patients |
| GET | `/patients/{id}/crossrefs` | All system references for a patient |
| POST | `/patients/match` | Probabilistic matching |
| GET | `/crossrefs` | All cross-references |
| POST | `/sync` | Manual crossref sync |

### Integration Hub — `/integration-hub/api`

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service health + sync counters |
| GET | `/feed` | Recent sync events |
| POST | `/poll` | Trigger a manual poll cycle |
| GET | `/audit` | Sync audit log |

### HL7 — `/hl7/api`

| Method | Path | Description |
|---|---|---|
| GET | `/messages` | Message log |
| POST | `/messages/inbound` | Submit HL7 v2 without MLLP |
| POST | `/messages/outbound/adt` | Build + send outbound ADT |
| POST | `/messages/outbound/oru` | Build + send outbound ORU |

---

## Event Bus

The platform event bus is a Redis Stream (`openhis:events`). Services publish events after successful operations; subscribers consume them via consumer groups.

### Event types

| Event | Publisher | Subscribers | Payload fields |
|---|---|---|---|
| `patient.synced` | integration-hub | mpi, analytics | `omrs_id`, `oe_id`, `mrn` |
| `lab_order.routed` | integration-hub | analytics | `omrs_id`, `oe_id` |
| `lab_result.ready` | integration-hub | analytics | `oe_id`, `subject` |

### Watching events live

```bash
# Via the admin SSE endpoint (EventSource-compatible)
curl -N http://localhost/admin/api/events/stream

# Direct Redis inspection
docker exec -it openhis-redis-1 redis-cli XRANGE openhis:events - + COUNT 20
```

---

## Generating Sample Data

### DICOM (Imaging profile)

1. Open the DICOM Simulator at `http://localhost/simulator/`
2. Select modality (CR, DX, CT, MR, US, NM) and configure parameters
3. Click **Generate & Push** — instances are created in memory and sent to Orthanc
4. The AI Controller auto-queues a pipeline job if a matching rule exists

```bash
curl -X POST http://localhost/simulator/api/generate \
  -H "Content-Type: application/json" \
  -d '{"modality":"CR","patient":{"patientname":"DOE^JOHN","patientid":"P001"},"params":{"bodypart":"CHEST"}}'
```

### Patients (OpenMRS)

Use the OpenMRS O3 Registration app at `http://localhost/openmrs/spa/` or the REST API:

```bash
curl -u admin:Admin123 -X POST http://localhost/openmrs/ws/rest/v1/patient \
  -H "Content-Type: application/json" \
  -d '{"person":{"names":[{"givenName":"Jane","familyName":"Doe"}],"birthdate":"1985-03-15","gender":"F"},"identifiers":[{"identifier":"P-001","identifierType":"05a29f94-c0ed-11e2-94be-8c13b969e334","location":"44c3efb0-2583-4c80-a79e-1f756a03c0a1"}]}'
```

---

## AI Pipelines

Two proof-of-concept pipelines run as isolated Docker containers spawned by the AI Controller (imaging profile required).

| Pipeline | Modalities | Output |
|---|---|---|
| `poc-xray` | CR, DX | Findings JSON + overlay DICOM |
| `poc-ct` | CT | Findings JSON + segmentation mask DICOM |

Findings are randomly generated for demonstration purposes.

### Adding a new pipeline

1. Create `pipelines/<name>/Dockerfile` + `run.py` (reads `$JOB_ID`, writes `result.json`)
2. Add a build entry to `compose/profiles/imaging.yml`
3. Register: `POST /ai-controller/api/pipelines`
4. Add trigger rule: `POST /ai-controller/api/rules`

---

## Configuration

Copy `.env.example` to `.env`. All variables have safe defaults for local development.

### Platform

| Variable | Default | Description |
|---|---|---|
| `OPENHIS_PROFILES` | `emr,laboratory,erp,imaging,analytics` | Active profiles (comma-separated) |

### Credentials

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `orthanc` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `orthanc` | PostgreSQL password |
| `ADMIN_USER` | `admin` | Admin dashboard username |
| `ADMIN_PASS` | `admin123` | Admin dashboard password |
| `KEYCLOAK_ADMIN` | `admin` | Keycloak admin username |
| `KEYCLOAK_ADMIN_PASSWORD` | `admin` | Keycloak admin password |
| `KEYCLOAK_CLIENT_SECRET` | `openhis-platform-secret` | Keycloak OIDC client secret |

### Services

| Variable | Default | Description |
|---|---|---|
| `ROOT_PATH` | `/<service>` | FastAPI root path — must match nginx location prefix |
| `REDIS_URL` | `redis://redis:6379` | Redis event bus URL |
| `POLL_INTERVAL_S` | `60` | Integration-hub sync interval (seconds) |
| `COLLECT_INTERVAL_MIN` | `5` | Analytics collection interval (minutes) |
| `MLLP_PORT` | `2575` | HL7 MLLP listener port |
| `SESSION_TTL_HOURS` | `12` | Admin session TTL |
| `ORTHANC_URL` | `http://orthanc:8042` | Orthanc REST base URL |
| `JOBS_DATA_DIR` | `/data/jobs` | AI pipeline job I/O volume path |
| `JOBS_VOLUME_NAME` | `openhis_ai-jobs` | Docker volume name for pipeline artifacts |
| `DOCKER_NETWORK` | `openhis_openhis-net` | Docker network for spawned pipeline containers |

---

## Data Persistence

All stateful data lives in named Docker volumes.

| Volume | Service | Contents |
|---|---|---|
| `pg-data` | postgres | Orthanc index, OpenELIS schema |
| `admin-data` | admin | Users, config, audit log |
| `mpi-data` | mpi | Patient crossrefs + match candidates |
| `hl7-data` | hl7 | HL7 message store |
| `hub-audit` | integration-hub | Sync audit log |
| `redis-data` | redis | Event stream (AOF) |
| `orthanc-data` | orthanc | DICOM pixel data |
| `ai-jobs` | ai-controller + pipelines | Pipeline job I/O artifacts |

```bash
# Full reset — destroys ALL data
make clean
# or: docker compose -f compose/base.yml down -v
```

---

## Developer Commands

```bash
# Start / stop
make up                       # Start base + active profiles (reads OPENHIS_PROFILES from .env)
make down                     # Stop all (keep volumes)
make build                    # Rebuild all images
make up-build                 # Rebuild and start

# Profile shortcuts
make emr-up                   # Start base + EMR profile
make lab-up                   # Start base + Laboratory profile
make imaging-up               # Start base + Imaging profile
make erp-up                   # Start base + ERP profile
make analytics-up             # Start base + Analytics profile
make base-up                  # Start base stack only

# Single service
make restart SVC=admin        # Restart one service
make logs                     # Tail all logs
make logs-service SVC=mpi     # Tail logs for one service
make ps                       # Show service status

# Cleanup
make clean                    # Stop and remove all volumes (destructive)
```

Rebuild and restart a single service after a code change:

```bash
docker compose -f compose/base.yml build admin
docker compose -f compose/base.yml up -d --no-deps admin
```

---

## Testing

```bash
# Full suite
make test
# or: python -m pytest tests/ -v

# Single service
make test-service SVC=mpi
# or: python -m pytest tests/mpi/ -v
```

Tests use in-memory or `/tmp` SQLite databases and do not require running containers.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `http://localhost` returns 502 for a profile service | Profile not enabled | Run `python platform/opm.py enable <profile>` then `make up` |
| OpenMRS SPA returns 404 | `openmrs-frontend` container not running | Ensure `emr` profile is active; wait ~3 min for Liquibase startup |
| OpenELIS login fails | Container still starting | OpenELIS takes 3–5 min on first start; watch `make logs-service SVC=openelis` |
| Odoo shows blank page | `odoo-db` not initialised | Wait 2–3 min; check `docker compose logs odoo-db` |
| Admin SSE stream disconnects immediately | Redis not running | Verify `docker ps | grep redis`; check `REDIS_URL` env var |
| Keycloak returns 404 on `/keycloak/` | `KC_HTTP_RELATIVE_PATH` not set | Already set in base.yml; restart keycloak container |
| nginx returns 502 for all services | nginx config out of sync with profiles | Run `python platform/opm.py nginx --reload` |
| `opm enable` complains about unknown profile | Typo in profile name | Valid names: `emr`, `laboratory`, `erp`, `imaging`, `analytics` |
| DICOM push to Orthanc fails | Orthanc not running | Ensure `imaging` profile is active |
| Pipeline containers fail to start | Wrong volume/network name | Verify `JOBS_VOLUME_NAME=openhis_ai-jobs` and `DOCKER_NETWORK=openhis_openhis-net` |
| OpenAPI docs show wrong base path | `ROOT_PATH` not set | Each FastAPI service needs `ROOT_PATH=/<service>` matching its nginx location prefix |
