# PACS Demo — Full-Stack Radiology Platform

A self-contained, Docker-based radiology demo stack including a DICOM server,
acquisition simulator, RIS, AI analysis service, and OHIF viewer — all wired
together and accessible through a single nginx reverse proxy.

---

## Architecture

```
Browser
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│                     nginx  :80                          │
│   /          → OHIF Viewer                              │
│   /orthanc/  → Orthanc DICOM server                     │
│   /simulator/→ Acquisition Simulator                    │
│   /ris/      → Radiology Information System             │
│   /ai/       → AI Analysis Service                      │
│   /ai-panel.html → AI Results Panel                     │
└───┬──────────┬──────────┬──────────┬────────────────────┘
    │          │          │          │
    ▼          ▼          ▼          ▼
  OHIF      Orthanc   Simulator    RIS          AI Service
  :80        :8042      :8001      :8002          :8000
               │                                   ▲
               │  OnStoredInstance                 │
               └───────────── plugin.py ───────────┘
               │
               ▼
           PostgreSQL
              :5432
```

---

## Services at a Glance

| URL | Service | Description |
|-----|---------|-------------|
| `http://localhost/` | **OHIF Viewer** | Zero-footprint DICOM viewer (v3.9) |
| `http://localhost/orthanc/` | **Orthanc** | DICOM server + REST API + Explorer UI |
| `http://localhost/simulator/` | **Simulator** | Generate & send synthetic DICOMs |
| `http://localhost/ris/` | **RIS** | Worklist · Orders · Reports |
| `http://localhost/ai/health` | **AI Service** | Analysis API health check |
| `http://localhost/ai-panel.html?instanceId=<id>` | **AI Panel** | Per-instance AI results |

DICOM C-STORE (for real modalities): **port 4242**

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) ≥ 24 or Docker Engine + Compose v2
- 4 GB RAM recommended (Orthanc + OHIF + AI service)
- Ports **80** and **4242** free on localhost

---

## Quick Start

```bash
# 1. Clone / unzip the project
cd pacs-demo

# 2. Build and start all services
docker compose up --build -d

# 3. Wait ~30 s for Orthanc and OHIF to become ready
docker compose ps          # all should show "healthy" or "running"

# 4. Open the portal
open http://localhost/
```

> **First boot note:** PostgreSQL initialises its data directory on first run.
> If Orthanc starts before Postgres is ready it will restart once automatically —
> this is expected and handled by the `depends_on: condition: service_healthy` rule.

---

## End-to-End Workflow

### 1 — Generate a synthetic DICOM

1. Open **`http://localhost/simulator/`**
2. Click a modality card (e.g. **CT**)
3. Set Body Part → `CHEST`, Priority → `ROUTINE`
4. Fill in patient details (or leave defaults)
5. Click **⚡ Generate & Send to Orthanc**

The simulator builds a valid DICOM with synthetic pixel data and POSTs it
directly to Orthanc. A result card appears with three links:

| Link | Destination |
|------|-------------|
| **Open in OHIF →** | Launches the study in the viewer |
| **AI Results →** | Opens the AI analysis panel for this instance |
| **Orthanc Explorer ↗** | Raw instance view in Orthanc |

---

### 2 — Review AI Analysis

The Orthanc plugin fires automatically on every stored instance.
By the time you click **AI Results →** the analysis is usually already done.

The **AI Panel** (`/ai-panel.html?instanceId=...`) shows:

- Patient / modality / body-part info bar
- Colour-coded **impression** (green = normal, orange = abnormal, red = critical)
- **Findings list** — each finding has a severity icon, type tags, optional
  measurements, and an animated confidence bar
- **Push to RIS Report** button — creates a pre-filled DRAFT report in the RIS
  using the AI findings as a starting point

---

### 3 — Manage the Worklist (RIS)

Open **`http://localhost/ris/`**

#### Import patients from Orthanc
1. Click the **Patients** tab
2. Click **⟳ Sync from Orthanc** — all patients stored in Orthanc are imported

#### Create an imaging order
1. Click **+ New Order** (Worklist tab)
2. Select a patient, modality, body part, priority
3. Click **Create Order** — the order appears in the worklist table

#### Write a radiology report
1. Find the order row → click **📄 Report**
2. Fill in Technique · Findings · Impression · Recommendation
3. Workflow buttons:

| Button | Status transition |
|--------|-------------------|
| **💾 Save Draft** | Saves as `DRAFT` |
| **📋 Preliminary** | Promotes to `PRELIMINARY` |
| **✅ Finalize** | Locks to `FINAL`, marks order `COMPLETED`, sets `finalized_at` |
| **＋ Save Addendum** | Appends dated note (only on `FINAL` reports) |
| **🖨 Print** | Opens browser print dialog with clean serif layout |

> **Tip:** After clicking **Push to RIS Report** in the AI panel, the draft is
> already pre-filled — just review and finalize.

---

### 4 — View in OHIF

Open **`http://localhost/`** — all studies stored in Orthanc appear automatically
via DICOMweb (WADO-RS). Click any study to open it in the viewer.

---

## Supported Modalities

| Modality | SOP Class | Pixel Engine | AI Findings Pool |
|----------|-----------|--------------|-----------------|
| **CR** | Computed Radiography | 12-bit X-ray (body-part aware) | Chest: opacity, nodule, effusion, cardiomegaly, PTX |
| **DX** | Digital X-ray | 12-bit X-ray (body-part aware) | Same as CR |
| **CT** | CT Image | 16-bit HU axial slices × N | Chest / Head / Abdomen pools |
| **MR** | MR Image | 12-bit T1/T2 slices × N | Brain / Spine / Knee pools |
| **US** | Ultrasound | 8-bit B-mode fan | Abdomen / Thyroid pools |

CT and MR generate one DICOM instance **per slice** — all sharing the same
`StudyInstanceUID` and `SeriesInstanceUID`.

---

## API Reference

### AI Service (`/ai/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ai/health` | Service status + cached result count |
| `POST` | `/ai/analyze` | Queue analysis `{"instance_id":"..."}` |
| `GET` | `/ai/results` | List all cached results, newest first (used by all-patients view) |
| `GET` | `/ai/results/{id}` | Get result for one instance |
| `DELETE` | `/ai/results/{id}` | Clear cached result (forces re-analysis) |

### RIS (`/ris/api/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/worklist` | Filtered orders + patient + report status |
| `POST` | `/orders` | Create imaging order |
| `GET/PUT` | `/orders/{id}` | Read / update order |
| `GET` | `/patients` | List patients |
| `POST` | `/patients/sync` | Import patients from Orthanc |
| `GET` | `/reports` | List reports (filter by `?status=`) |
| `GET` | `/reports/order/{id}` | Get report for an order |
| `POST` | `/reports` | Create report |
| `PUT` | `/reports/{id}` | Update / finalize report |

### Orthanc REST API (`/orthanc/`)

Full Orthanc REST API is proxied at `/orthanc/` — see
[Orthanc REST API docs](https://orthanc.uclouvain.be/api/) for the complete
reference. Commonly used endpoints:

```
GET  /orthanc/patients          → list all patients
GET  /orthanc/instances/{id}    → instance metadata
GET  /orthanc/instances/{id}/file → raw DICOM bytes
POST /orthanc/instances         → upload DICOM (Content-Type: application/dicom)
```

---

## Data Persistence

| Volume | Mount | Contents |
|--------|-------|----------|
| `pg-data` | postgres:/var/lib/postgresql | Orthanc index |
| `orthanc-data` | orthanc:/var/lib/orthanc/db | DICOM pixel data |
| `ris-data` | ris:/data/ris.db | SQLite RIS database |

**Reset everything:**
```bash
docker compose down -v   # removes all volumes — full clean slate
docker compose up -d
```

**Keep data, restart services:**
```bash
docker compose restart
```

---

## Useful Commands

```bash
# Live logs for a specific service
docker compose logs -f ai-service
docker compose logs -f ris
docker compose logs -f orthanc

# Rebuild a single service after code change
docker compose up --build -d simulator

# Restart without rebuild (Python/HTML file changes only)
docker compose restart ris
docker compose restart simulator

# Open a shell inside the AI service
docker compose exec ai-service bash

# Query the RIS database directly
docker compose exec ris sqlite3 /data/ris.db ".tables"
docker compose exec ris sqlite3 /data/ris.db "SELECT * FROM orders;"

# Check Orthanc plugin loaded correctly
docker compose logs orthanc | grep "AI analysis plugin"
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `/ris/` returns 502 | RIS container not ready | `docker compose restart ris` |
| AI Results shows "processing" forever | AI service unreachable | Check `docker compose logs ai-service` |
| OHIF shows no studies | Orthanc not healthy | `docker compose logs orthanc` — wait for Postgres |
| Simulator "Orthanc unreachable" dot | Orthanc still starting | Wait 20 s, reload |
| Port 80 already in use | Another web server running | Stop it or change nginx port in `docker-compose.yml` |
| Port 4242 in use | Another DICOM listener | Change `"4242:4242"` mapping |

---

## Project Structure

```
pacs-demo/
├── docker-compose.yml
├── nginx/
│   └── nginx.conf
├── orthanc/
│   ├── orthanc.json          # Orthanc configuration
│   └── plugin.py             # AI webhook (OnStoredInstance)
├── simulator/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py               # FastAPI — POST /api/generate
│   ├── presets.py            # Modality configurations
│   ├── dicom_factory.py      # Pixel generators (CR/DX/CT/MR/US)
│   └── static/index.html     # Simulator UI
├── ris/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py               # FastAPI app
│   ├── database.py           # SQLite schema + helpers
│   ├── routers/
│   │   ├── patients.py       # GET /patients, POST /patients/sync
│   │   ├── orders.py         # Worklist + CRUD
│   │   └── reports.py        # Report editor + finalization
│   └── static/index.html     # RIS UI (Worklist · Patients · Reports)
├── ai-service/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py               # FastAPI — /analyze, /results
│   └── analyzers/
│       ├── __init__.py       # Dispatch + pydicom parsing
│       ├── xray.py           # CR / DX findings
│       ├── ct.py             # CT findings (chest/head/abdomen)
│       ├── mr.py             # MR findings (brain/spine/knee)
│       └── us.py             # US findings (abdomen/thyroid)
└── ohif/
    ├── app-config.js         # OHIF DICOMweb config
    └── ai-panel.html         # AI Results Panel (all-patients + per-instance)
```

---

*Built step by step — Step 1 (infrastructure) → Step 2 (simulator) →
Step 3 (RIS) → Step 4 (AI service + all-patients diagnostic panel).*
