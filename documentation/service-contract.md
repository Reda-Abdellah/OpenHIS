# OpenHIS Service Contract

Every native service (FastAPI-based) in the OpenHIS platform must satisfy this contract to be managed by OPM and visible in the admin dashboard.

---

## 1. Manifest — `openhis.service.json`

Each service directory must contain a manifest file at its root:

```json
{
  "name":         "my-service",
  "display_name": "My Service",
  "port":         8020,
  "profile":      "base",
  "nginx_path":   "/my-service/",
  "health_path":  "/api/health",
  "bus": {
    "publishes":  ["event.type"],
    "subscribes": ["other.event"]
  },
  "depends_on":   ["redis"],
  "description":  "What this service does."
}
```

| Field          | Required | Description |
|----------------|----------|-------------|
| `name`         | yes      | Unique identifier, matches the Docker Compose service name |
| `port`         | yes      | Container-internal HTTP port |
| `profile`      | yes      | Compose profile that activates this service (`base` = always-on) |
| `nginx_path`   | yes      | nginx location prefix (trailing slash required) |
| `health_path`  | yes      | Path that returns `{"status": "ok"}` |
| `bus.publishes`| yes      | Event types this service writes to `openhis:events` |
| `bus.subscribes`| yes     | Event types this service consumes (use `"*"` for all) |
| `depends_on`   | yes      | Runtime dependencies by service name |

---

## 2. Health endpoint

`GET {health_path}` must:

- Return HTTP **200** and a JSON body containing `"status": "ok"` when the service is ready.
- Return HTTP **503** (or any non-200) when degraded — the admin dashboard marks it red.
- Respond within **3 seconds** — health probes time out at 5 s.

Minimal response:

```json
{"status": "ok", "service": "my-service", "version": "1.0.0"}
```

---

## 3. Root path awareness

Services are served under a subpath by nginx. The FastAPI app **must** use `root_path`:

```python
ROOT_PATH = os.environ.get("ROOT_PATH", "")
app = FastAPI(root_path=ROOT_PATH)
```

The compose file injects `ROOT_PATH=/my-service` so OpenAPI docs and redirect URLs resolve correctly.

---

## 4. Structured logging

Log to stdout in the format uvicorn uses by default. Do **not** log to files inside the container — use the audit DB or the bus for persistent records.

---

## 5. Graceful shutdown

FastAPI's `@app.on_event("shutdown")` must close any open connections (Redis, DB, HTTP clients). The compose `stop_grace_period` is 10 s — services that don't shut cleanly within that window are killed.

---

## 6. Compose entry

Add the service to the appropriate profile file under `compose/profiles/`:

```yaml
services:
  my-service:
    build: ../../services/my-service
    environment:
      ROOT_PATH: /my-service
      REDIS_URL: redis://redis:6379
    networks: [openhis-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8020/api/health"]
      interval: 20s
      timeout: 5s
      retries: 3
      start_period: 15s
```

Update the `x-openhis.nginx_routes` block in that profile file to add the nginx route.

---

## 7. Scaffold with OPM

`opm add-service <name>` generates a compliant skeleton (main.py, Dockerfile, requirements.txt, openhis.service.json). Modify as needed then add it to the correct profile compose file.
