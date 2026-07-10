# Changelog

All notable changes to OpenHIS are documented here.
This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- integration-hub context surface (`GET /api/context/diagnostic-report/{oe_id}`): hub-mediated, audit-logged OpenELIS read for native services, gated with the `internal-sync` machine role — used by the hl7 `lab_result.ready` consumer to build outbound ORU^R01 without direct OpenELIS access
- MPI FHIR R4 facade: PDQm-flavored `Patient` search plus IHE PIXm-style `$ihe-pix` cross-reference query (`services/mpi/routers/fhir.py`)
- CI-independent deny-by-default auth harness (`pytest tests/auth`): every native service booted with real RS256 JWT validation and asserted for the full 401/403/2xx triple
- Bus payload-contract tests pinning the exact field sets of `lab_order.routed` and `lab_result.ready`
- `openhis-opm` packaging: PyPI-ready distribution with a working `opm` console script and `opm --version`; French quickstart guide (`docs/quickstart.md`)
- `make backup` / `make restore`: profile-aware backup & restore tooling (`scripts/backup.sh`, `scripts/restore.sh`) with `--dry-run` plans, sha256 manifest, and a compose-driven completeness self-test
- MPI matching accuracy benchmark (`pytest tests/benchmarks`): precision/recall regression floors on a corpus of real-world name variants (diacritics, Arabic/French transliterations); methodology and figures in `docs/benchmarks/mpi-matching.md`
- `openhis_sdk.metrics`: Prometheus metrics for native services — `MetricsMiddleware` records `openhis_http_requests_total` and `openhis_http_request_duration_seconds` labeled by service/method/route-template/status; `metrics_router` exposes `GET /metrics` (JWT-exempt, in-network scrape only); pull-based `openhis_dlq_depth{stream}` gauge XLENs `openhis:events:dlq` at scrape time; `prometheus_client` backend with a zero-dependency text-exposition fallback; example alert rules in `infra/prometheus/alerts-example.yml`
- `integration-hub` service: bidirectional FHIR R4 sync between OpenMRS, OpenELIS, and Odoo
- Keycloak SSO with JWT middleware across all services
- `compose/profiles/` for selective stack deployment (emr, laboratory, imaging, erp, analytics)
- `services/_legacy/` folder to freeze `ehr`, `lis`, `pharmacy`, `fhir-bridge`

### Changed
- SDK bus consumers now ack only after successful handling — failed events stay pending, are retried via `XAUTOCLAIM`, and land on the bounded `openhis:events:dlq` dead-letter stream after `max_delivery` attempts (ADR 0005)
- Event-bus `MAXLEN` unified at 50 000 via `openhis_sdk.bus.MAXLEN`

- `services-legacy/` renamed to `services/_legacy/` to signal frozen status
- Documentation reorganized from flat `documentation/` into `docs/` with planning, adr, and reference sections
- Tests reorganized into `tests/unit/`, `tests/integration/`, and `tests/smoke/`

### Fixed
- DEF-004: MPI `matcher.find_candidates` no longer self-filters every candidate when neither the query nor the pool entries carry an id (guard is now `pid is not None and p.get("id") == pid`)
- DEF-001: integration-hub adapter health checks no longer require a Keycloak token — upstream liveness is probed unauthenticated, so "Keycloak down" is no longer misreported as "upstream down"
- DEF-007: analytics service no longer refuses every feature call with "KEYCLOAK_URL missing"
- DEF-008: outbound HL7 messages now persist `patient_id`/`patient_name` via the shared PID parser (ADT/ORU/ORM send routes and the bus-consumer outbound path)
- DEF-010: MPI now publishes `patient.synced` on the bus after REST patient create/update/merge and after cross-reference creation (new `services/mpi/bus.py`, fire-and-forget after the DB commit); the integration-hub's new `patient.synced` consumer (`app/bus_consumer.py`) resolves the master record + cross-references from the MPI (`internal-sync` machine read) and upserts the patient into OpenELIS — live confirmation blocked by DEF-012 (OpenELIS FHIR store)
- V-01 live validation fixes: `KEYCLOAK_AUDIENCE=openhis-platform` set for every `*-sa` service in compose (tokens carry the platform audience; the SDK otherwise expects `aud=<client_id>` and rejects every valid token); nginx `auth_request_set` renamed `$remote_user` → `$auth_user` (built-in variable collision crashed nginx at startup); `token.py` renamed `sa_token.py` in analytics/hl7/ris (stdlib `token` shadowing broke the analytics collector and hl7 imports — T-17); SDK redis pinned `<6` + consumer treats blocking-read timeouts as empty reads; analytics metrics API aligned with the V&V vocabulary (`patients`/`lab`/`imaging` aliases, default trend params, alias-aware export)
- New defects surfaced by V-01 and registered: DEF-011 (hub↔OpenMRS FHIR sync rejected under oauth2login SSO), DEF-012 (OpenELIS FHIR façade requires a backing FHIR store)
- DEF-012: new `oe-fhir-store` HAPI FHIR JPA server in the laboratory profile — OpenELIS's FHIR module is a façade that forwards every search/write to `org.openelisglobal.fhirstore.uri`; the store is addressed via the subnet-restricted `/oe-fhir-store/` nginx route (OE's JVM resolves only `localhost`). Closes DEF-010's live confirmation: e2e S1.6 (MPI patient → hub consumer → OpenELIS) now passes hard
- OpenELIS adapter: `upsert_patient` no longer treats OE's empty-body 201 as a failure (the id rides in the Location header) — that bug made the bus consumer retry and create duplicate patients; the hub consumer additionally keeps a `master_id → oe_id` map in Redis because OE re-keys posted identifiers (`pat_guid`/`pat_uuid`) in its FHIR projection, so re-emitted events skip instead of duplicating
- `pytest tests/unit tests/integration` in a single invocation (= `make test`) no longer fails with `RuntimeError: There is no current event loop`

### Security
- T-02: ai-controller lockdown — pipeline/rules/jobs/saveback routes role-gated; pipeline containers restricted to the `POC_ALLOWED_IMAGES` allowlist and run with memory/CPU/pids caps plus `no-new-privileges`
- T-03: every admin router mutation now requires auth + roles and writes an audit row (closes DEF-002)
- T-05: RIS and MPI routes now enforce role checks (`require_roles`) on reads and writes
- T-06: integration-hub event-ingest endpoints gated with the `internal-sync`/`admin` machine roles; DICOM simulator locked to dev mode + auth
- MPI matcher (T-16): duplicate threshold 0.70 → 0.75 (env-overridable via `MPI_MATCH_THRESHOLD`), diacritics transliterated instead of dropped, Metaphone phonetic floor for spelling variants (ADR 0006)
- Infra hardening pass: all hardcoded compose credentials externalized to env vars with dev-safe defaults (`${VAR:-dev_value}` — OpenMRS, OpenELIS, Odoo); Redis AUTH support via `REDIS_PASSWORD` (empty = dev unchanged); Keycloak realm and OpenELIS `extra.properties` now rendered from `.j2` templates by `opm init` (rendered files gitignored — no client secrets in git); Orthanc production config with `AuthenticationEnabled` + plugin Keycloak client-credentials auth (`orthanc-sa` service account); ai-controller reaches Docker through a least-privilege `docker-socket-proxy` instead of mounting `/var/run/docker.sock`; self-signed dev TLS cert generator (`scripts/gen_dev_certs.sh`)
- nginx njs guard now verifies RS256 JWT signatures against the Keycloak JWKS (was structure/expiry only) and is wired via `auth_request`; machine-to-machine FHIR routes restricted to the pinned `openhis-net` subnet (172.28.0.0/16)
- HL7 MLLP port 2575 no longer host-published — internal-only by default, deliberate re-exposure via `compose/overrides/mllp-public.yml` (T-08)
- `make up` now refuses to start when the rendered Keycloak realm is missing, pointing at `opm init` (prevents silent every-login-fails deployments)
- `DEV_MODE=true` (JWT bypass) now refuses to boot unless `ENV=development` — the bypass is structurally unreachable in staging/production

### Removed
- Standalone `ehr`, `lis`, `pharmacy` services as primary deployments (replaced by OpenMRS / OpenELIS / Odoo)

---

## [0.5.0] — 2025-01-01

### Added
- OpenMRS 3 + OpenELIS + Odoo 17 as primary clinical applications
- NGINX JWT authentication and role-based access control
- AI controller with CT/X-ray pipeline workers

---

_Full release history available in the [git log](https://github.com/your-org/openhis/commits/main)._
