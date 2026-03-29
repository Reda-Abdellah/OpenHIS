# Changelog

All notable changes to OpenHIS are documented here.
This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- `integration-hub` service: bidirectional FHIR R4 sync between OpenMRS, OpenELIS, and Odoo
- Keycloak SSO with JWT middleware across all services
- `compose/profiles/` for selective stack deployment (emr, laboratory, imaging, erp, analytics)
- `services/_legacy/` folder to freeze `ehr`, `lis`, `pharmacy`, `fhir-bridge`

### Changed
- `services-legacy/` renamed to `services/_legacy/` to signal frozen status
- Documentation reorganized from flat `documentation/` into `docs/` with planning, adr, and reference sections
- Tests reorganized into `tests/unit/`, `tests/integration/`, and `tests/smoke/`

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
