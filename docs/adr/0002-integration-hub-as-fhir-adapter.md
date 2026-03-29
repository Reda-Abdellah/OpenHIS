# ADR-0002: Integration Hub as the FHIR R4 Adapter

**Status:** Accepted
**Date:** 2025-01-01

## Context

OpenHIS integrates three primary clinical applications — OpenMRS (EMR),
OpenELIS (LIS), and Odoo (ERP) — each with its own data model and API.
Cross-system data flows (patient sync, lab results, medication orders) need a
reliable, auditable integration layer.

Options evaluated:
- **Direct service-to-service REST calls** — simple but creates a spider-web of
  dependencies; every service must know about every other.
- **Mirth Connect / Rhapsody** — dedicated integration engines; heavyweight and
  require separate licensing.
- **Custom `integration-hub` service** — thin FastAPI service that owns all
  cross-system mappings and publishes events to the bus.

## Decision

Build a dedicated **`integration-hub`** service that:

1. Exposes FHIR R4 read/write endpoints as a unified API surface.
2. Polls OpenMRS (`/openmrs/ws/fhir2/R4/`) and OpenELIS (`/fhir/R4/`) on a
   configurable interval and syncs changes bidirectionally.
3. Publishes domain events (`patient.synced`, `lab.result.ready`, etc.) to the
   Redis Streams bus so downstream services (MPI, analytics, HL7 gateway) react
   without polling.
4. Maintains an audit log (`hub-audit` volume) of every cross-system write.

## Consequences

- **Single integration point** — all cross-system logic lives in one place;
  individual services remain domain-focused.
- **Polling latency** — near-real-time but not instant; default poll interval is
  60 s. Acceptable for clinical coordination; not suitable for sub-second alerts.
- **FHIR R4 as the canonical format** — downstream services must map to/from
  FHIR, which adds initial complexity but enables future interoperability
  (external EHR federation, national HIE).
- **Single point of failure** — if `integration-hub` is down, cross-system sync
  pauses. Mitigate with health checks, restart policies, and the audit log for
  replay.

## Implementation

See `services/integration-hub/` and the hub Compose service in
[compose/base.yml](../../compose/base.yml).
