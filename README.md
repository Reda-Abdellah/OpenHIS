# PACS Demo – Full-Stack Radiology Platform

A self-contained, Docker-based radiology demo stack including a DICOM server,
acquisition simulator, RIS, AI controller with pipeline support, and OHIF viewer —
all wired together behind a single nginx reverse proxy.

---

## Quick start

```bash
# 1. Make sure nothing is already using port 4242 (DICOM) or 80 (HTTP)
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep -E "4242|:80"

# If the old stack is still running, stop it first
docker compose down   # run from the old project directory if needed

# 2. Build all images (including pipeline containers)
docker compose build

# 3. Start the stack
docker compose up -d

# 4. Follow logs (optional)
docker compose logs -f
```

---

## Service URLs

| Service | URL | Notes |
|---|---|---|
| **OHIF Viewer** | http://localhost/ | DICOMweb viewer, catch-all route |
| **AI Controller** | http://localhost/ai-controller/ | Pipeline manager UI (new) |
| **Simulator** | http://localhost/simulator/ | Generate synthetic DICOM acquisitions |
| **RIS** | http://localhost/ris/ | Radiology Information System (worklist, reports) |
| **Orthanc** | http://localhost/orthanc/ | DICOM server web UI |
| **Orthanc DICOM** | `localhost:4242` | C-STORE / C-FIND (raw DICOM port) |

> All HTTP services share **port 80** through nginx. Only the DICOM port `4242` is
> exposed separately. If `4242` is already allocated on your host, change the mapping
> in `docker-compose.yml` under the `orthanc` service: `"4243:4242"`.

---

## Architecture

```
Browser
   │
   └─ nginx :80
        ├─ /                  →  OHIF Viewer
        ├─ /ai-controller/    →  AI Controller  :8000
        ├─ /simulator/        →  Simulator      :8001
        ├─ /ris/              →  RIS            :8002
        └─ /orthanc/          →  Orthanc        :8042
                                      │
                              PostgreSQL :5432
                              (study/series store)
                                      │
                              Orthanc plugin.py
                              (OnStoredInstance)
                                      │
                              AI Controller :8000
                              ├─ SQLite DB  (/data/ai-controller.db)
                              ├─ Jobs data  (/data/jobs  ← ai-jobs volume)
                              └─ docker.sock → pipeline containers
                                    ├─ pacs-demo/poc-xray:latest
                                    └─ pacs-demo/poc-ct:latest
```

---

## AI Controller – Pipeline system

### How it works

1. **Orthanc plugin** (`orthanc/plugin.py`) fires on every stored DICOM instance and
   calls `POST /api/trigger-instance` on the AI Controller.
2. The controller **resolves the series**, matches it against enabled **rules**
   (modality + body part filters), and creates a **job** for each matching pipeline
   (deduplicated: one job per series × pipeline).
3. The **runner** downloads the full DICOM series into
   `/data/jobs/{job_id}/input/`, writes `input.json`, then calls
   `docker run <pipeline-image>` via the Docker socket.
4. The pipeline container reads the input, runs its analysis, and writes
   `output/result.json` + optional `*.dcm` files.
5. The controller registers all **artifacts**, stores a result summary, and
   optionally **saves output DICOMs back to Orthanc** (auto or manual).

### Pipeline contract

Every pipeline image must:

| | Path inside container |
|---|---|
| Read DICOM series | `/data/jobs/$JOB_ID/input/*.dcm` |
| Read metadata | `/data/jobs/$JOB_ID/input/input.json` |
| Write report | `/data/jobs/$JOB_ID/output/result.json` |
| Write derived DICOM (optional) | `/data/jobs/$JOB_ID/output/*.dcm` |

Minimum `result.json` schema:
```json
{
  "pipeline_id": "my-pipeline",
  "normal": true,
  "critical": false,
  "findings": [
    {
      "id": 1,
      "type": "opacity",
      "description": "Right lower lobe opacity",
      "location": "RLL",
      "severity": "moderate",
      "confidence": 0.87,
      "measurements": { "area_cm2": 3.2 }
    }
  ],
  "impression": "...",
  "follow_up_recommended": false,
  "output_files": ["overlay_001.dcm"]
}
```

### POC pipelines (included)

| Image | ID | Trigger rule | Output |
|---|---|---|---|
| `pacs-demo/poc-xray:latest` | `poc-xray` | CR or DX, body part CHEST/THORAX | `overlay_001.dcm` (Secondary Capture) + `result.json` |
| `pacs-demo/poc-ct:latest` | `poc-ct` | CT, body part CHEST | `seg_mask_001.dcm` (colour mask SC) + `result.json` |

Both pipelines generate **random seeded findings** — they are skeletons showing
the pipeline contract, not real AI models.

---

## AI Controller UI pages

| Page | Path | Description |
|---|---|---|
| Dashboard | `/ai-controller/` | Live stats, recent jobs, auto-refresh |
| Pipelines | `#pipelines` | List pipelines, enable/disable toggle |
| Rules | `#rules` | Manage auto-trigger + auto-saveback rules per pipeline |
| Jobs | `#jobs` | Filterable job list; click a row for full detail |
| PACS Browser | `#pacs` | Browse Orthanc series, trigger a pipeline manually |
| Job Detail | modal | Input/output artifacts, findings, impression, container logs, save-to-PACS button |

---

## AI Controller API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Service status + counts |
| `GET` | `/api/pipelines` | List all pipelines |
| `PATCH` | `/api/pipelines/{id}` | Update pipeline (e.g. `{"enabled": 0}`) |
| `GET` | `/api/rules` | List all rules (filter: `?pipeline_id=`) |
| `POST` | `/api/rules` | Create rule |
| `PATCH` | `/api/rules/{id}` | Update rule |
| `DELETE` | `/api/rules/{id}` | Delete rule |
| `GET` | `/api/jobs` | List jobs (filter: `?pipeline_id=&status=`) |
| `POST` | `/api/jobs` | Manually trigger a job |
| `GET` | `/api/jobs/{id}` | Job detail with artifacts + saveback events |
| `DELETE` | `/api/jobs/{id}` | Delete job |
| `GET` | `/api/artifacts/{id}/download` | Download artifact file |
| `POST` | `/api/saveback` | Manually save a DICOM artifact back to Orthanc |
| `GET` | `/api/orthanc/series` | Browse Orthanc series for manual triggering |
| `POST` | `/api/trigger-instance` | Webhook called by Orthanc plugin |

---

## Adding a real pipeline

1. Create `pipelines/my-pipeline/Dockerfile` + `run.py` following the contract above.
2. Add the build service to `docker-compose.yml`:
   ```yaml
   my-pipeline:
     build: ./pipelines/my-pipeline
     image: pacs-demo/my-pipeline:latest
     restart: "no"
     command: ["true"]
     networks: [pacs-net]
   ```
3. Build: `docker compose build my-pipeline`
4. Register in the AI Controller UI (Pipelines page → add) or seed in `database.py`.
5. Add a routing rule on the Rules page (modality, body part, auto-trigger).

---

## Port conflict troubleshooting

```bash
# Find what holds port 4242
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep 4242
sudo lsof -i :4242          # Linux/macOS

# Stop a previous stack
docker compose -p pacs-demo down

# Or remap the DICOM port in docker-compose.yml
# ports:
#   - "4243:4242"   ← host port 4243, container still uses 4242 internally
```

---

## Volumes

| Volume | Contents |
|---|---|
| `pg-data` | Orthanc PostgreSQL database |
| `orthanc-data` | Orthanc file store |
| `ris-data` | RIS SQLite database |
| `ai-jobs` | Job input/output DICOM files (shared with pipeline containers) |
| `ai-controller-db` | AI Controller SQLite database |
