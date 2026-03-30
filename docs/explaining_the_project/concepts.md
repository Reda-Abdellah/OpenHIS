# OpenHIS — Concepts & Project Goals

> This document defines what OpenHIS **is**, what it is **not**, and why it exists.  
> It is the authoritative reference for contributors, maintainers, and external evaluators.

---

## What OpenHIS Is

OpenHIS is an **open-source health information platform** — not an EMR, not a LIS, not an ERP.

Its role is to turn a curated set of best-of-breed open-source clinical systems (OpenMRS, OpenELIS, Odoo, Orthanc, OHIF, …) into a **cohesive, operationally managed, and interoperable hospital stack** that any health facility can deploy, customise, and extend — with a single command.

The platform owns:
- **How modules are composed and deployed** (profiles, OPM CLI)
- **How data flows between them** (FHIR integration spine, Redis event bus)
- **How patients are identified across all of them** (Master Patient Index)
- **How the whole system is operated** (admin plane, SSO, health monitoring, audit)

The platform does **not** own clinical domain logic — it delegates that to the applications it integrates.

---

## Three Governing Principles

Every architectural decision in OpenHIS follows these rules:

1. **Integration over reimplementation**  
   Use mature open-source applications for clinical domain logic (OpenMRS for EMR, OpenELIS for laboratory, Odoo for finance). Build only what does not already exist: the integration layer, the identity spine, the deployment engine, and the admin plane.

2. **Contracts over direct calls**  
   No service calls another service directly. All cross-system communication goes through the FHIR bridge or the Redis event bus, with documented payload schemas and adapter contracts. This keeps every module replaceable.

3. **Platform-first**  
   The admin plane, profile system, and service registry are built before individual module features. The platform must be operable as a whole before any module is useful in production.

---

## Core Concepts

### 1. Profile-Driven Deployment

A **profile** is a named, self-contained bundle of services that can be enabled or disabled as a unit.

```
base        → postgres, redis, nginx, keycloak, mpi, integration-hub, hl7, admin  (always on)
emr         → OpenMRS + required adapters
laboratory  → OpenELIS + LIS adapter
imaging     → Orthanc, OHIF, RIS, AI controller
erp         → Odoo + ERP adapter
analytics   → analytics collector, patient portal
```

Profiles are composed with Docker Compose and controlled by the **OPM CLI** (`platform/opm.py`).  
An operator selects which modules their facility needs; the platform handles routing, configuration, and service dependencies automatically.

**Goal:** `opm enable emr laboratory` is a complete, working deployment command for a clinic.

Each profile carries machine-readable metadata (port mappings, Nginx routes, dependencies, RAM footprint) so the platform can validate, route, and document itself without manual intervention.

---

### 2. FHIR Integration Spine

The **integration-hub** service is the single integration point for all cross-system data flows.

It:
- Exposes **FHIR R4** read/write endpoints as the unified API surface
- Polls OpenMRS and OpenELIS FHIR endpoints on a configurable interval and syncs changes bidirectionally
- Publishes **domain events** onto the Redis event bus after each successful sync
- Maintains a persistent **audit log** of every cross-system write

No service calls the OpenMRS API, OpenELIS API, or Odoo API directly. All such calls go through the adapters inside `integration-hub/app/services/`. This keeps integration logic in one place and makes each connected application replaceable.

**FHIR R4 is the canonical data format** for all cross-system data exchange. Downstream services must map to/from FHIR, which adds initial complexity but enables future interoperability (national HIE, external EHR federation, HL7 message routing).

---

### 3. Redis Event Bus

All reactive, cross-service coordination happens through a shared **Redis Streams** event bus (`openhisevents` stream, consumer groups per service).

This replaces direct service-to-service calls for asynchronous flows (lab result available → HL7 outbound → analytics counter increment → AI inference trigger). Each service subscribes to only the events it cares about; producers never need to know about consumers.

**Canonical events:**

| Event | Meaning |
|---|---|
| `patient.synced` | A patient record has been created or updated across systems |
| `lab.order.routed` | A lab order has been forwarded to OpenELIS |
| `lab.result.ready` | A lab result from OpenELIS is available |
| `dicom.stored` | A DICOM study has been stored in Orthanc |
| `radiology.report.ready` | A radiologist has finalized a report in RIS |
| `ai.result.ready` | An AI inference pipeline has completed |
| `odoo.patient.synced` | A patient has been synced to Odoo |

Adding a new module means **subscribing to existing events or publishing new ones** — not wiring direct HTTP calls between services.

---

### 4. Master Patient Index (MPI) as Identity Spine

A patient exists in multiple systems simultaneously: as an OpenMRS UUID, an OpenELIS accession number, an Odoo partner ID. Without a shared identity layer, there is no reliable way to join clinical data across systems or detect duplicates.

The **MPI service** is the identity spine of the platform. It:
- Assigns a stable `master_id` (UUID) to every patient at registration
- Maintains a `cross_references` table mapping `(system, system_id) → master_id`
- Exposes a REST API so any service can resolve all system-specific IDs from an MRN
- Supports **probabilistic patient matching** (name/DOB/sex fuzzy match with reviewable candidate scores)
- Subscribes to `patient.synced` events to keep cross-references current asynchronously

The MRN (Medical Record Number) is the matching key between systems. It is assumed to be assigned before a patient enters any downstream system.

**Goal:** Any service resolves `GET /api/cross-ref?system=OPENMRS_ID&value=<uuid>` and gets back all linked system IDs in one call.

---

### 5. Service Contracts

OpenHIS is extensible by design. There are three defined extension patterns, each with a formal contract:

**Adapter contract** (wrapping a third-party app):  
Implement `upsert_patient`, `get_patient`, and `healthcheck` as async functions in `integration-hub/app/services/<app>.py`. All error handling and retry is the platform's responsibility; adapters must never swallow exceptions silently.

**Service contract** (adding a new native FastAPI service):  
Every native service must have a `main.py` with lifespan context, an `openhis.service.json` manifest (name, profile, port, bus topics, required env vars), a `Dockerfile`, and tests in `tests/unit/<service>/`. Use OPM scaffolding: `opm add-service my-service --profile analytics --port 8099`.

**Profile contract** (composing a new deployable bundle):  
A profile YAML carries an `x-openhis` metadata block declaring its display name, dependencies, database names, and Nginx routes. The OPM profile engine reads this to validate, route, and document the profile automatically.

These contracts make the **platform's extension surface explicit and auditable** — a contributor knows exactly what to implement, and the platform can validate compliance in CI.

---

### 6. Unified Admin Plane

The **admin service** is the single pane of glass for operating the whole platform.

It provides:
- **SSO and identity**: Keycloak-backed user management, role assignment across all apps (admin, clinician, radiologist, lab-tech, pharmacist, patient)
- **Platform topology**: live graph of active profiles, services, health status, and inter-service dependencies
- **Profile management**: enable/disable profiles with pre-flight dependency checks and RAM estimates
- **System configuration**: centralised key-value store for platform-wide settings, with full audit trail
- **Live event stream**: real-time Redis stream feed for operational visibility
- **Announcements**: platform-wide broadcast with severity levels
- **Service health monitoring**: registry-driven health polling of all active services

The admin plane is **itself a module** governed by the same service contract. It does not have privileged access to other service internals — it reads the service registry, polls health endpoints, and consumes events just like any other service.

---

### 7. PHI Safety and Auditability

OpenHIS processes Protected Health Information (PHI). The platform is designed with an explicit security posture:

- **Fail loudly**: every service exits at startup if a required environment variable is missing — no silent degraded modes
- **Fail closed**: JWT validation returns `503 Identity provider not configured` if Keycloak is unreachable — never fail-open
- **Audit everything**: the integration-hub writes an audit entry for every cross-system write (success, failure, retry, exhaustion)
- **Retry safely**: failed syncs are queued with exponential backoff (max 5 attempts); exhausted items are recorded and never silently dropped
- **No credentials in code**: all secrets via environment variables; CI blocks any `CHANGEMEBEFOREDEPLOY` token in `.env` at deploy time
- **Trusted-network model**: OpenHIS is designed for deployment inside a hospital intranet or private VPC, not exposed directly to the public internet

---

## What OpenHIS Is Not

| It is not… | Because… |
|---|---|
| An EMR | OpenMRS handles clinical documentation, patient registration, SOAP notes, orders |
| A LIS | OpenELIS handles specimen management, instrument interfacing, result validation |
| A PACS | Orthanc + OHIF handle DICOM storage and diagnostic viewing |
| A billing system | Odoo handles invoicing, financial reporting, ERP workflows |
| A Bahmni fork | Bahmni is an EMR-centric distribution; OpenHIS is a profile-driven integration platform built around FHIR and an event bus, not Atom Feeds, and is designed to be modular rather than monolithic |

---

## How to Think About OpenHIS When Contributing

When you open a PR, ask:

1. **Does this belong in a module or in the platform?**  
   Clinical logic (encounter forms, lab panels, billing rules) → the module. Integration contracts, deployment, identity, observability → the platform.

2. **Does this add a direct service-to-service call?**  
   If yes, it probably belongs in an adapter in `integration-hub/` and should publish an event after completion, not call another service's HTTP endpoint directly.

3. **Is the extension point documented?**  
   New adapters → adapter contract. New services → service contract. New profiles → profile contract. If the pattern doesn't fit an existing contract, open a discussion before implementing.

4. **Is the audit trail maintained?**  
   Any cross-system write must produce an audit log entry. Any new required env var must be declared in `openhis.service.json` and in `.env.example`.

---

*See also: @docs/explaining-the-project/architecture.md, @docs/adr/, @docs/explaining-the-project/adding-a-module.md*
