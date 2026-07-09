# OpenHIS Architecture

## Overview

OpenHIS is a **middleware platform**, not a monolith. It provides:

1. **An event bus** (Redis Streams) — clinical events flow between services
   without point-to-point coupling
2. **A FHIR/REST integration hub** — adapters translate proprietary APIs of
   OpenMRS, OpenELIS, Orthanc, and Odoo into a normalised FHIR R4 surface
3. **A Master Patient Index (MPI)** — single source of truth for patient
   identity across all modules
4. **An Admin plane** — health monitoring, service registry, profile management,
   and audit log aggregation
5. **An OPM CLI** — operators enable/disable modules and the stack reconfigures
   itself (nginx routing, env vars, Docker Compose profiles)

## Component Map

```
┌──────────────────────────────────────────────────────────────────┐
│                        OPERATOR PLANE                            │
│   OPM CLI ──────────────────────────── Admin UI (port 80/admin) │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                         CORE SERVICES                            │
│                                                                  │
│  ┌──────────┐  ┌──────────────────┐  ┌────────┐  ┌──────────┐   │
│  │   MPI    │  │ Integration Hub  │  │  HL7   │  │  Admin   │   │
│  │ :8001    │  │ :8000 (FHIR/REST)│  │ :2575  │  │  :8080   │   │
│  └──────────┘  └────────┬─────────┘  └────────┘  └──────────┘   │
│                         │                                        │
│              ┌──────────▼──────────┐                            │
│              │   Redis Streams     │  ← Event bus backbone      │
│              │  openhis.events     │                            │
│              │  openhis.audit      │                            │
│              └─────────────────────┘                            │
└──────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                       OPTIONAL MODULES                           │
│                                                                  │
│  EMR Profile:   OpenMRS (:8081)                                  │
│  Lab Profile:   OpenELIS (:8082)                                 │
│  Imaging:       Orthanc (:8042) + OHIF (:3000) + RIS (:8002)    │
│                 + AI Controller (:8004)                          │
│  ERP:           Odoo (:8069)                                     │
│  Analytics:     Analytics service (:8003) + Patient Portal       │
└──────────────────────────────────────────────────────────────────┘
```

## Event Bus

All inter-service communication uses Redis Streams with consumer groups.

| Stream key | Published by | Consumed by |
|---|---|---|
| `patient.registered` | Integration Hub | MPI |
| `patient.synced` | MPI | Analytics, HL7 |
| `lab_order.routed` | Integration Hub | HL7, Analytics |
| `lab_result.ready` | Integration Hub | HL7, Patient Portal |
| `dicom.stored` | Integration Hub | AI Controller, Analytics |
| `radiology.report.ready` | RIS | HL7, Patient Portal |
| `ai.result.ready` | AI Controller | RIS, Integration Hub |
| `odoo.patient.synced` | Integration Hub | Analytics |
| `openhis.audit` | All services | Admin |

## Data Flow: Patient Registration

```
1. Clinician creates patient in OpenMRS
2. OpenMRS webhook → Integration Hub adapter
3. Hub normalises to FHIR Patient resource
4. Hub upserts patient in OpenELIS and Odoo
5. Hub publishes patient.synced → Redis
6. MPI consumer creates/updates identity record + cross-refs
7. Analytics consumer updates aggregate counters
```

## Service Contract

Every native service must implement:

- `GET /api/health` — returns `{"status": "ok"|"degraded"|"offline"}`
- `GET /api/version` — returns `{"service": "...", "version": "..."}`
- `openhis.service.json` manifest in the service root

See [adding-a-module.md](adding-a-module.md) for the full contract.

## Architectural Decisions

See [docs/adr/](../adr/) for the rationale behind key design choices.
