<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# What do you recommend in terms of codebase and docs organization

Here is a comprehensive recommendation for both the codebase layout and documentation structure.

***

## Recommended Repository Layout

The current repo mixes concerns in ways that will hurt contributors — services that should be retired live next to active ones, platform tooling is in a flat `platform/` folder with no packaging, and there's no clear signal about what is "core" vs. "optional." The goal of the reorganization is that a new contributor should be able to understand the project's boundaries within 5 minutes of cloning.

### Proposed Top-Level Structure

```
openhis/
├── compose/
│   ├── base.yml                 # Always-on: postgres, redis, nginx, keycloak, mpi, hub, hl7, admin
│   ├── profiles/
│   │   ├── emr.yml
│   │   ├── laboratory.yml
│   │   ├── imaging.yml
│   │   ├── erp.yml
│   │   └── analytics.yml
│   └── overrides/
│       ├── production.yml       # Hardens Keycloak, TLS, no start-dev
│       └── ci.yml               # Lightweight ports, no volumes for CI smoke tests
│
├── libs/
│   └── openhis_sdk/             # NEW: shared Python library
│       ├── pyproject.toml
│       └── src/openhis_sdk/
│           ├── auth.py          # JWT validation (canonical source)
│           ├── bus.py           # Redis stream publish/consume base
│           ├── logging.py       # Structured JSON logging setup
│           ├── retry.py         # withretry() decorator
│           └── middleware.py    # Request logging FastAPI middleware
│
├── services/
│   ├── admin/                   # Core — always deployed
│   ├── mpi/                     # Core
│   ├── integration-hub/         # Core
│   ├── hl7/                     # Core
│   ├── ris/                     # Optional (imaging profile)
│   ├── analytics/               # Optional (analytics profile)
│   ├── ai-controller/           # Optional (imaging profile)
│   ├── patient-portal/          # Optional (analytics profile)
│   └── _legacy/                 # FROZEN — not deployed by default
│       ├── ehr/
│       ├── lis/
│       └── pharmacy/
│
├── pipelines/                   # AI pipeline workers (unchanged)
│   ├── poc-ct/
│   └── poc-xray/
│
├── infra/
│   ├── nginx/
│   ├── keycloak/
│   ├── postgres/
│   ├── orthanc/
│   ├── ohif/
│   ├── openelis/
│   └── ssl/
│
├── platform/
│   ├── pyproject.toml           # NEW: makes opm installable via pip
│   ├── opm.py
│   ├── profileengine.py
│   ├── nginxgen.py
│   └── registry.py
│
├── tests/
│   ├── unit/                    # Fast, no Docker, per-service
│   │   ├── mpi/
│   │   ├── hl7/
│   │   ├── ris/
│   │   └── ...
│   ├── integration/             # Cross-service flows, uses respx mocks
│   │   ├── test_patient_registration_flow.py
│   │   ├── test_lab_result_flow.py
│   │   └── test_imaging_order_flow.py
│   └── smoke/                   # Docker Compose up + health checks
│       └── test_stack_health.py
│
├── docs/                        # NEW: replaces scattered .md files
│   ├── architecture.md
│   ├── profiles.md
│   ├── adding-a-module.md
│   ├── security.md
│   ├── data-retention.md
│   └── adr/                     # Architectural Decision Records
│       ├── 0001-event-bus-redis-streams.md
│       ├── 0002-fhir-bridge-as-adapter-hub.md
│       └── 0003-mpi-as-identity-spine.md
│
├── .github/
│   ├── workflows/
│   │   ├── ci.yml               # Unit tests on every PR
│   │   ├── smoke.yml            # Docker stack health on merge to main
│   │   └── release.yml          # Build + push to GHCR on semver tag
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       ├── feature_request.md
│       └── new_module_proposal.md
│
├── .env.example
├── Makefile
├── docker-compose.yml           # Root orchestrator (reads OPENHIS_PROFILES)
├── README.md
├── CONTRIBUTING.md              # NEW
├── SECURITY.md                  # NEW
├── CHANGELOG.md                 # NEW
└── CODE_OF_CONDUCT.md           # NEW
```


***

## Key Codebase Changes

### The `_legacy/` Signal

Moving `ehr`, `lis`, and `pharmacy` into `services/_legacy/` sends an unambiguous signal to contributors: **these are frozen reference implementations.** The underscore prefix is a Python convention for "internal/deprecated" that most developers immediately recognize. Add a `_legacy/README.md` stating they exist for local dev reference only and will not be extended.[^1]

### Per-Service `openhis.service.json` as the Contract

Every service already has an `openhis.service.json` manifest — this is a strength. Standardize its schema so the OPM CLI and service registry can consume it automatically:

```json
{
  "name": "ris",
  "version": "0.3.0",
  "profile": "imaging",
  "port": 8002,
  "nginx_path": "ris",
  "health_path": "/api/health",
  "bus": {
    "publishes": ["radiology.report.ready"],
    "subscribes": []
  },
  "depends_on": ["mpi", "integration-hub"],
  "env_required": ["OPENMRS_URL", "FHIR_BRIDGE_URL"],
  "env_optional": ["POLL_INTERVAL_S"]
}
```

This becomes the **single source of truth** for routing, dependency checking, and the Admin topology view — OPM can read it directly instead of duplicating the information in the profile YAML.

### Test Directory Hygiene

The current `tests/` folder mixes unit, integration, and cross-service tests with no clear separation. The three-layer split above (`unit/`, `integration/`, `smoke/`) maps directly to three CI jobs with different triggers:[^1]


| Layer | Trigger | Docker? | Duration |
| :-- | :-- | :-- | :-- |
| `unit/` | Every commit | No | < 2 min |
| `integration/` | Every PR | Mocks only | < 5 min |
| `smoke/` | Merge to `main` | Full stack | < 15 min |


***

## Documentation Structure

The existing docs are scattered across the root (`README.md`, `DEMO.md`, `FEATURES.md`), a `documentation/` folder with long planning documents, and inline comments. The recommended `docs/` structure separates **stable references** from **planning artifacts**.

### Files to Keep (Move to `docs/`)

- `adapter-contract.md` → `docs/adding-a-module.md` (rename for discoverability)
- `service-contract.md` → merge into `docs/adding-a-module.md` as a section
- `profile-contract.md` → `docs/profiles.md`
- `DEMO.md` → `docs/demo-walkthrough.md`


### Files to Archive (Move to `docs/planning/`)

- `CohesionTransformationPlan.md` — valuable history but not a live reference
- `Planenhanceopenhis.md` — same
- `FEATURES.md` — replace with the GitHub Releases page


### Architectural Decision Records (ADRs)

The codebase contains several non-obvious architectural choices — Redis Streams over RabbitMQ, FHIR as the integration contract, MPI as the identity spine. These decisions are currently buried in planning documents. Moving them to short `docs/adr/` files gives future contributors the *why*, not just the *what*. A minimal ADR template:

```markdown
# ADR-0001: Redis Streams as the Event Bus

**Status:** Accepted  
**Date:** 2025-xx-xx

## Context
Multiple services need to react to clinical events (patient synced, lab result ready)
without tight coupling...

## Decision
Use Redis Streams with consumer groups...

## Consequences
- ✅ No additional infrastructure (Redis is already required for sessions)
- ✅ At-least-once delivery with consumer group ACKs
- ⚠️ Retention is memory-bounded; configure maxlen or AOF persistence
```


### README Restructuring

The current README is comprehensive but front-loads a long feature list before showing how to actually run the project. Reorder it:

```
1. What is OpenHIS? (2–3 sentences + architecture diagram)
2. Quick Start (opm init → opm enable emr → make up → open URLs)
3. Profiles (table: profile / modules / RAM / use case)
4. OPM CLI reference
5. Adding a module (link to docs/adding-a-module.md)
6. Contributing (link to CONTRIBUTING.md)
7. License + Security
```

The rule is: a reader should be able to run the stack within 10 minutes of opening the README for the first time, without reading anything else.

<div align="center">⁂</div>

[^1]: repomix-output.xml

