# PACS Demo

A self-contained, Dockerized medical imaging stack featuring DICOM storage (Orthanc),
a web viewer (OHIF v3), and an automatic X-ray AI inference pipeline powered by
TorchXRayVision. Designed for healthcare startup demos and proof-of-concept deployments.

---

## Architecture

```
Browser
  │
  ▼
nginx:80  ──/──────────────────▶  OHIF Viewer   (ohif:80)
          ──/orthanc/───────────▶  Orthanc REST  (orthanc:8042)
          ──/ai/────────────────▶  AI FastAPI    (ai-service:8000)
          ──/ai-panel.html──────▶  Static HTML   (served by nginx)

Orthanc ──on CR/DX stored──────▶  AI Service   (internal HTTP)
           ◀──predictions JSON──┘
           └──stored in metadata slot 9999

Orthanc ──index──▶  PostgreSQL 15  (postgres:5432)

Scanner / DICOM node ──▶  Orthanc DICOM port (0.0.0.0:4242)
```

| Service      | Image                     | Exposed port      |
|--------------|---------------------------|-------------------|
| nginx        | nginx:alpine              | **80** (HTTP)     |
| orthanc      | orthancteam/orthanc       | **4242** (DICOM)  |
| postgres     | postgres:15               | internal only     |
| ai-service   | custom (python:3.11-slim) | internal only     |
| ohif         | ohif/app:v3.9.2           | internal only     |

---

## Prerequisites

| Tool           | Minimum version |
|----------------|-----------------|
| Docker Engine  | 24.x            |
| Docker Compose | v2.x (`docker compose`) |
| Free disk      | ~5 GB (PyTorch CPU wheels + model weights) |
| RAM            | 4 GB recommended |

---

## Quick Start

### 1 — Clone and build

```bash
git clone <your-repo-url> pacs-demo
cd pacs-demo
docker compose up --build -d
```

The first build downloads ~2 GB of PyTorch CPU wheels and the DenseNet121 model
weights. Subsequent starts are fast.

### 2 — Wait for the AI service to be ready

```bash
docker compose logs -f ai-service
# Wait until you see: "Model loaded — pathologies: [...]"
```

### 3 — Place a sample DICOM

```bash
# Option A: let the test script auto-download a public CR DICOM
bash scripts/test_phase0.sh

# Option B: use your own file
cp /path/to/chest_xray.dcm scripts/sample.dcm
```

### 4 — Run the end-to-end AI pipeline test

```bash
bash scripts/test_phase1.sh
```

---

## URLs

| Endpoint                              | Description                        |
|---------------------------------------|------------------------------------|
| `http://localhost/`                   | OHIF DICOM viewer                  |
| `http://localhost/orthanc/app/explorer.html` | Orthanc web console         |
| `http://localhost/orthanc/`           | Orthanc REST API                   |
| `http://localhost/ai/health`          | AI service health check            |
| `http://localhost/ai/predict`         | AI inference endpoint (POST)       |
| `http://localhost/ai-panel.html?instanceId=<ID>` | AI results panel      |

---

## File Tree

```
pacs-demo/
├── docker-compose.yml        # All services, volumes, networks
├── nginx/
│   └── nginx.conf            # Reverse proxy + static file serving
├── orthanc/
│   ├── orthanc.json          # Orthanc base configuration
│   └── plugin.py             # Python plugin: triggers AI on CR/DX storage
├── ai-service/
│   ├── Dockerfile            # python:3.11-slim + PyTorch CPU + app
│   ├── requirements.txt      # FastAPI, pydicom, torchxrayvision, etc.
│   ├── main.py               # FastAPI app: /health + /predict
│   └── inference.py          # TorchXRayVision DenseNet121 inference logic
├── ohif/
│   ├── app-config.js         # OHIF v3 DICOMweb data source config
│   └── ai-panel.html         # Standalone AI results viewer (vanilla JS)
└── scripts/
    ├── test_phase0.sh        # Upload test + connectivity check
    └── test_phase1.sh        # Full AI pipeline test with result display
```

---

## AI Pipeline

1. A DICOM instance is stored in Orthanc (via REST POST or DICOM C-STORE).
2. `plugin.py` fires `OnStoredInstanceCallback`.
3. If `Modality` tag is `CR` or `DX`, the raw DICOM bytes are POSTed to the AI
   service at `http://ai-service:8000/predict` using `http.client` (no external
   libraries — compatible with Orthanc's Python sandbox).
4. `inference.py` reads the pixel array with pydicom, normalises it to
   `[-1024, 1024]`, resizes to 224×224, and runs a forward pass through
   DenseNet121 (`densenet121-res224-all` weights).
5. The service returns `{ predictions: {...}, top3: [...] }`.
6. The plugin stores the JSON in Orthanc metadata slot `9999` and logs the top-3
   findings.
7. Open `http://localhost/ai-panel.html?instanceId=<ID>` to view results.

> **Safety**: all AI logic in the plugin is wrapped in `try/except`. An AI
> failure never crashes Orthanc.

---

## API Reference

### `GET /ai/health`

```json
{ "status": "ok", "model": "densenet121-res224-all" }
```

### `POST /ai/predict`

| Field  | Type              | Description              |
|--------|-------------------|--------------------------|
| `file` | multipart/form-data | Raw DICOM file bytes   |

**Response 200**

```json
{
  "predictions": {
    "Atelectasis": 0.2341,
    "Cardiomegaly": 0.1823,
    "Effusion": 0.4102,
    ...
  },
  "top3": [
    { "pathology": "Effusion",     "probability": 0.4102 },
    { "pathology": "Atelectasis",  "probability": 0.2341 },
    { "pathology": "Cardiomegaly", "probability": 0.1823 }
  ]
}
```

**Response 422** — file is not valid DICOM.

---

## Sending DICOM from a Scanner / PACS Node

Orthanc listens on port 4242 with AET `ORTHANC`. Configure your modality:

| Setting   | Value     |
|-----------|-----------|
| AET       | `ORTHANC` |
| Host/IP   | `<host-ip>` |
| Port      | `4242`    |

Or upload via REST:

```bash
curl -X POST http://localhost/orthanc/instances \
     --data-binary @image.dcm \
     -H "Content-Type: application/dicom"
```

---

## Configuration

### Environment variables (docker-compose.yml)

| Variable                        | Default          | Description              |
|---------------------------------|------------------|--------------------------|
| `ORTHANC__POSTGRESQL__HOST`     | `postgres`       | PostgreSQL hostname      |
| `ORTHANC__POSTGRESQL__DATABASE` | `orthanc`        | Database name            |
| `ORTHANC__POSTGRESQL__USERNAME` | `orthanc`        | Database user            |
| `ORTHANC__POSTGRESQL__PASSWORD` | `orthanc`        | Database password        |
| `ORTHANC__PYTHON_SCRIPT`        | `/etc/orthanc/plugin.py` | Plugin path    |
| `AI_SERVICE_URL`                | `http://ai-service:8000` | AI endpoint   |

### Changing the AI model

Edit `inference.py` and update `MODEL_NAME`. Available TorchXRayVision weights:

- `densenet121-res224-all` (default — trained on all datasets)
- `densenet121-res224-chex`
- `densenet121-res224-nih`
- `densenet121-res224-rsna`

---

## Useful Commands

```bash
# View all logs
docker compose logs -f

# View only AI plugin activity in Orthanc
docker compose logs -f orthanc | grep AI-Plugin

# Restart only the AI service (e.g. after code change)
docker compose up -d --build ai-service

# Stop everything (keeps volumes)
docker compose down

# Stop and wipe all data (destructive)
docker compose down -v

# Query stored studies
curl -s http://localhost/orthanc/studies | python3 -m json.tool

# Fetch AI result for a known instance
curl http://localhost/orthanc/instances/<INSTANCE_ID>/metadata/9999
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| AI panel shows "metadata not found" | Check the file is CR or DX modality; check `docker compose logs orthanc \| grep AI-Plugin` |
| `ai-service` unhealthy after 2 min | Model weights still downloading; wait and recheck with `docker compose ps` |
| OHIF shows blank study list | Ensure DICOMweb QIDO path resolves: `curl http://localhost/orthanc/dicom-web/studies` |
| Port 80 already in use | Change nginx host port in `docker-compose.yml`: `"8080:80"` |
| Port 4242 already in use | Change orthanc DICOM host port: `"4243:4242"` |

---

## Security Notice

Authentication is **disabled** for demo purposes. Before any production or
patient-data use:

- Enable Orthanc authentication (`AuthenticationEnabled: true` in `orthanc.json`)
- Add TLS termination in nginx
- Restrict DICOM port 4242 to trusted network segments
- Replace hardcoded PostgreSQL credentials with Docker secrets

---

## License

This project is provided as a demo scaffold. TorchXRayVision is released under
the MIT License. Orthanc is licensed under GPLv3. OHIF is licensed under MIT.
