# Adding a Module to OpenHIS

This guide explains how to integrate a new clinical application or service
into the OpenHIS platform. It covers both **third-party app adapters** and
**new native FastAPI services**.

---

## Option A — Adapter for a Third-Party App

Use this when you want to integrate an existing open-source application
(e.g., a pharmacy system, a scheduling app) without writing a new service.

### 1. Create the adapter file

```
services/integration-hub/adapters/<appname>.py
```

The adapter must implement three functions:

```python
async def upsert_patient(patient: FHIRPatient) -> str:
    """Create or update a patient. Return the app's internal patient ID."""

async def get_patient(internal_id: str) -> FHIRPatient:
    """Fetch a patient by the app's internal ID, return as FHIR Patient."""

async def health_check() -> bool:
    """Return True if the app is reachable and healthy."""
```

All HTTP calls must go through the `withretry` decorator from `openhis_sdk.retry`.

### 2. Register the adapter

Add the adapter to `services/integration-hub/adapters/__init__.py` and
register it in the adapter registry in `services/integration-hub/registry.py`.

### 3. Add env vars

Declare required env vars in `services/integration-hub/openhis.service.json`
under `env_required` and add them to `.env.example` with comments.

### 4. Write tests

Add `tests/unit/integration-hub/test_<appname>_adapter.py`.
Mock all HTTP calls with `respx`. Do not test against a live instance.

---

## Option B — New Native Service

Use this when you need a new backend service (e.g., a notification service,
a custom analytics worker).

### 1. Scaffold the service

```bash
opm add-service <name> --profile <profile> --port <port>
```

This creates:

```
services/<name>/
├── main.py                  # FastAPI app with lifespan context
├── routers/
│   └── health.py            # GET /api/health — pre-wired
├── openhis.service.json     # Service manifest
├── Dockerfile
└── tests/
    └── test_<name>.py
```

### 2. Service Manifest (`openhis.service.json`)

Fill in all fields:

```json
{
  "name": "my-service",
  "version": "0.1.0",
  "profile": "analytics",
  "port": 8099,
  "nginx_path": "my-service",
  "health_path": "/api/health",
  "bus": {
    "publishes": ["my.event.name"],
    "subscribes": ["patient.synced"]
  },
  "depends_on": ["mpi"],
  "env_required": ["MY_SERVICE_SECRET"],
  "env_optional": ["MY_POLL_INTERVAL_S"]
}
```

### 3. Use the shared SDK

```python
from openhis_sdk.auth import require_token
from openhis_sdk.logging import configure
from openhis_sdk.bus import publish, consume
from openhis_sdk.retry import withretry

configure("my-service")  # Call before app = FastAPI()
```

**Do not copy** `jwtauth.py`, `logconfig.py`, or `busconsumer.py` from another
service. Always import from `openhis_sdk`.

### 4. Implement the bus consumer (if subscribing)

```python
# In your lifespan context manager:
async with consume("patient.synced", group="my-service") as stream:
    async for event in stream:
        await dispatch(event)
        await stream.ack(event)
```

### 5. Add the service to a Compose profile

Add the service to `compose/profiles/<your-profile>.yml` and to
`platform/profileengine.py`.

---

## Service Contract Requirements

Every native service MUST:

- [ ] Implement `GET /api/health` returning `{"status": "ok"}`
- [ ] Implement `GET /api/version` returning `{"service": "...", "version": "..."}`
- [ ] Call `configure(service_name)` from `openhis_sdk.logging` at startup
- [ ] Import auth from `openhis_sdk.auth`, not a local copy
- [ ] Validate all required env vars at startup — `sys.exit(1)` if missing
- [ ] Have an `openhis.service.json` manifest with all fields populated
- [ ] Have unit tests with > 60% coverage on router handlers
