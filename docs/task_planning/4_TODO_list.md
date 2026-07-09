<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# Could you create a clear and well-detailed to-do list with obejectifs than split them into tasks

Here is the full OpenHIS to-do list, organized by objective with granular tasks. Each objective is self-contained so your team can pick them up independently.

***

## 🔒 OBJ 1 — Security Hardening

*Priority: Critical — must be done before any clinical pilot or public release*

**1.1 — Strip hardcoded credentials**

- [ ] Grep all services for `os.getenv("...", "Admin123")` and similar defaults; remove every non-empty second argument
- [ ] Update `.env.example` — replace all demo values with `CHANGEME_BEFORE_DEPLOY` and add a warning block at the top
- [ ] Verify `.gitignore` explicitly blocks `.env` and `.env.*` files
- [ ] Add a startup `sys.exit()` guard in each service `main.py` that lists all required env vars and fails loudly if any are `None`
- [ ] Add a unit test per service that mocks env vars to `None` and asserts `SystemExit` is raised

**1.2 — Fix the JWT fail-open vulnerability**

- [ ] In every `jwtauth.py` copy, change the `if not KEYCLOAK_URL: return {}` branch to raise `HTTP 503 Identity provider not configured` instead of silently passing
- [ ] Add a developer-mode env flag `OPENHIS_DEV_SKIP_AUTH=1` as the **only** sanctioned way to bypass auth in local dev, with a startup warning printed to logs
- [ ] Write a pytest test that verifies a missing `KEYCLOAK_URL` returns 503, not 200

**1.3 — Harden the MLLP HL7 listener**

- [ ] Add a configurable `MLLP_MAX_MSG_BYTES` cap (default 1 MB) before passing data to the HL7 parser
- [ ] Add a per-connection read timeout (default 30s) using `asyncio.wait_for`
- [ ] Wrap the MLLP `asyncio` server with `ssl.create_default_context()` using a configurable cert path; document the self-signed cert setup for local dev
- [ ] Add `MLLP_MAX_MSG_BYTES`, `MLLP_TLS_CERT`, and `MLLP_TLS_KEY` to `.env.example` with comments

**1.4 — Harden Keycloak for non-dev deployments**

- [ ] Replace `start-dev` with `start` in the Keycloak `docker-compose` command for the `production` compose override
- [ ] Create `compose/overrides/production.yml` that replaces dev-mode defaults (Keycloak, Postgres passwords, `ssl_required`)
- [ ] Document the `production.yml` override in the README with a clear "DO NOT use base stack in production" callout

***

## 🧱 OBJ 2 — Shared Internal SDK

*Priority: High — eliminates drift across 10+ services*

**2.1 — Create `libs/openhis_sdk`**

- [ ] Create `libs/openhis_sdk/` directory with `pyproject.toml` and `src/openhis_sdk/` layout
- [ ] Move the canonical `jwtauth.py` into `openhis_sdk.auth` — the version in `services/admin/` is the most complete
- [ ] Move the canonical `logconfig.py` into `openhis_sdk.logging`
- [ ] Move the Redis bus publish/consume pattern into `openhis_sdk.bus` (parametrize stream name, group, consumer)
- [ ] Move the `withretry` decorator from `integration-hub/utils/retry.py` into `openhis_sdk.retry`

**2.2 — Wire SDK into all services**

- [ ] Add `libs/openhis_sdk` as a volume mount in each service's `Dockerfile` (`COPY ../../libs/openhis_sdk /openhis_sdk && pip install -e /openhis_sdk`)
- [ ] Replace per-service copies of `jwtauth.py` with `from openhis_sdk.auth import require_token` in: `mpi`, `hl7`, `analytics`, `ai-controller`, `ris`, `integration-hub`, `patient-portal`
- [ ] Replace per-service copies of `logconfig.py` with `from openhis_sdk.logging import configure` in all services
- [ ] Delete the now-redundant per-service copies; add a CI lint step that fails if `jwtauth.py` or `logconfig.py` appear outside `libs/`

***

## 🚌 OBJ 3 — Event Bus Completion

*Priority: High — currently events are produced but partially dropped*

**3.1 — Implement missing Redis Stream consumers**

- [ ] Add `busconsumer.py` to `services/analytics` — subscribe to `patient.synced`, `laborder.routed`, `labresult.ready`, `dicom.stored`; write aggregate counters to the analytics DB
- [ ] Add `busconsumer.py` to `services/hl7` — subscribe to `labresult.ready`; build and send outbound ORU^R01 to downstream systems
- [ ] Add `busconsumer.py` to `services/ai-controller` — subscribe to `dicom.stored`; trigger AI inference job for the referenced Study UID
- [ ] Wire each consumer into its service's `lifespan` context manager (replace any deprecated `@app.on_event`)
- [ ] Write unit tests for each consumer's `dispatch()` function using a mocked Redis client

**3.2 — Publish missing events from `integration-hub`**

- [ ] After successful Orthanc DICOM webhook processing, publish `dicom.stored` to the bus with `studyuid`, `patientid`, `modality`, `ts`
- [ ] After RIS report finalization, publish `radiology.report.ready` with `reportid`, `studyuid`, `patientid`
- [ ] After AI result save-back, publish `ai.result.ready` with `jobid`, `studyuid`, `pipeline`
- [ ] Add integration tests for each new event publish using `respx` mocks

**3.3 — Fix in-memory deduplication**

- [ ] Replace the `synced_patients: set`, `synced_orders: set`, `synced_reports: set` Python sets in `integration-hub/worker.py` with a Redis Set (`SADD / SISMEMBER`) so dedup survives restarts
- [ ] Add a TTL policy to Redis dedup keys (e.g., 7-day expiry with `EXPIRE`) to prevent unbounded growth
- [ ] Enable Redis AOF persistence in `infra/redis/redis.conf` (`appendonly yes`, `appendfsync everysec`)

***

## ⚙️ OBJ 4 — OPM \& Deployment Experience

*Priority: Medium — makes the platform genuinely operable*

**4.1 — Make `opm enable` actually start containers**

- [ ] In `opm.py enable`, after writing `.env` and regenerating nginx, call `docker compose up -d <services for added profile>` automatically
- [ ] Add a `--config-only` flag to preserve the existing "write .env only, no restart" behaviour for operators who want manual control
- [ ] Mirror the same logic in `opm disable` — stop containers for the removed profile automatically

**4.2 — Make `opm init` production-safe**

- [ ] Block `opm init --non-interactive` from setting any real password — force users to pass `--postgres-pass`, `--admin-pass`, `--keycloak-pass` as explicit CLI arguments or env vars
- [ ] Add a `--validate` flag that checks all `CHANGEME_BEFORE_DEPLOY` tokens are replaced before writing `.env`

**4.3 — Package OPM as an installable CLI**

- [ ] Add `pyproject.toml` in `platform/` with entry point `openhis-opm = platform.opm:cli`
- [ ] Publish to PyPI as `openhis-opm` (or register the name), enabling `pip install openhis-opm` without cloning the repo
- [ ] Add a GitHub Actions workflow step that publishes to PyPI on every semver tag push

**4.4 — MPI cross-reference population**

- [ ] After each successful `integration-hub` patient sync, POST the `openmrs_id ↔ openelis_id` pair to the MPI `/api/crossref` endpoint
- [ ] Add the Odoo partner ID mapping when the ERP adapter runs `upsert_patient`
- [ ] Add an integration test that creates a patient, runs the sync, and asserts the cross-ref entry appears in the MPI DB

***

## 🏥 OBJ 5 — Healthcare Compliance Foundations

*Priority: Medium — table-stakes before real clinical use*

**5.1 — Unified audit stream**

- [ ] Standardize audit event schema: `{ts, service, actor, action, resource_type, resource_id, outcome, detail}` across all services
- [ ] Publish every audit event to a dedicated Redis Stream `openhis.audit` (in addition to local DB writes)
- [ ] In the Admin service, add an `GET /api/audit/stream` SSE endpoint that proxies `openhis.audit` for cross-service audit viewing
- [ ] Add a data retention policy: archive audit logs older than 90 days to a JSON file and purge from DB (configurable via `AUDIT_RETENTION_DAYS` env var)

**5.2 — FHIR CapabilityStatement**

- [x] Add a `GET /fhir/metadata` endpoint to `integration-hub` returning a FHIR R4 `CapabilityStatement` JSON resource
- [x] Populate it with the resource types the hub actually handles: `Patient`, `DiagnosticReport`, `ImagingStudy`, `Observation`, `ServiceRequest`, `MedicationRequest`
- [x] Add a test asserting the response validates as a valid FHIR R4 CapabilityStatement using `fhir.resources` library

**5.3 — Data retention \& privacy**

- [ ] Add a TTL/archival policy document to `documentation/data-retention.md` covering Redis streams, SQLite audit tables, and Postgres tables
- [ ] Add `PATIENT_DATA_RETENTION_YEARS` env var to `.env.example` with a note about regulatory defaults (HIPAA: 6 years, EU: varies)

***

## 📖 OBJ 6 — Open-Source Readiness

*Priority: Medium — required to attract and retain contributors*

**6.1 — Contributor infrastructure**

- [ ] Write `CONTRIBUTING.md` covering: local dev environment setup (virtualenv + Docker), how to scaffold a new service with `opm add-service`, how to write tests, and the PR checklist
- [ ] Create `.github/ISSUE_TEMPLATE/bug_report.md`, `feature_request.md`, and `new_module_proposal.md`
- [ ] Create `SECURITY.md` with a responsible disclosure email and a policy statement about PHI in bug reports
- [ ] Add a `CODE_OF_CONDUCT.md` (Contributor Covenant is standard)

**6.2 — Release \& versioning**

- [ ] Add `CHANGELOG.md` (start from `v0.1.0`) following Keep a Changelog format
- [ ] Create the first annotated Git tag `v0.1.0-alpha`
- [ ] Add a GitHub Actions workflow that builds and pushes Docker images to GHCR on every semver tag: `ghcr.io/[org]/openhis-[service]:v0.1.0`
- [ ] Add a Release GitHub Action that auto-generates release notes from merged PRs

**6.3 — Documentation**

- [ ] Generate a static architecture SVG from the topology endpoint and embed it in `README.md`
- [ ] Add a "Prerequisites" section to `README.md` with links to Docker Compose v2 and Python 3.11 install guides
- [ ] Create a `docs/` directory and add `architecture.md`, `profiles.md`, and `adding-a-module.md`
- [ ] Record or create an animated GIF of `opm enable imaging` + Admin UI health view for the README hero

***

## 🧪 OBJ 7 — Test Coverage Gaps

*Priority: Medium — CI currently gives false confidence on several services*

**7.1 — Integration-hub test coverage**

- [ ] Add tests for the `openmrs.py` adapter using a Docker-based OpenMRS test instance (or `respx` mocks at the HTTP boundary — not at the adapter boundary per the adapter contract)
- [ ] Add tests for the `openelis.py` adapter
- [ ] Add end-to-end flow test: patient registered in OpenMRS → integration-hub syncs → patient appears in OpenELIS + MPI crossref

**7.2 — Admin service test coverage**

- [ ] Add tests for `routers/registry.py` — register service, deregister, health probe
- [ ] Add tests for `routers/profiles.py` — enable/disable profile writes to `.env` correctly
- [ ] Add a test asserting the audit log records every `POST`, `PUT`, `DELETE` action

**7.3 — CI smoke test job**

- [ ] Add a `smoke-test` CI job that runs `docker compose -f compose/base.yml up -d` and waits for all health endpoints to return 200 before marking the build green
- [ ] Add the smoke test as a required status check on `main` and `master` branches

***

## 🔭 OBJ 8 — Observability

*Priority: Low — nice to have for operators*

**8.1 — Structured logging consistency**

- [ ] Enforce `logconfig.configure(service_name)` is called in every service `main.py` before `FastAPI()` is instantiated
- [ ] Add `LOG_LEVEL` and `LOG_FORMAT` to `.env.example` with comments; default to `json` in production compose, `text` in dev
- [ ] Add request/response logging middleware (log method, path, status code, duration in ms) to all FastAPI services using a shared middleware from `openhis_sdk`

**8.2 — Metrics \& alerting foundation**

- [ ] Add a `GET /api/metrics` endpoint to each native service exposing Prometheus-compatible text format (use `prometheus_client` library) for: request count, error count, queue depth (Redis stream pending count)
- [ ] Add a `prometheus` scrape config to the `analytics` Compose profile
- [ ] Add an `OPENHIS_ALERT_WEBHOOK` env var; when a service's health probe returns `offline` for >2 consecutive checks, POST a JSON alert to the webhook URL (Slack/Teams compatible)
<span style="display:none">[^1]</span>

<div align="center">⁂</div>

[^1]: repomix-output.xml

