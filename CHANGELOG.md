# Changelog

All notable changes to OpenHIS are documented here.
This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
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

### Security
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
