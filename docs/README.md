# OpenHIS Documentation

Table of contents for everything under `docs/`. Directory names use
`snake_case`; file names use `kebab-case`.

## Start here

- [quickstart.md](quickstart.md) — from a bare 8 GB VPS to a running hospital stack in ~30 minutes (in French).
- [explaining_the_project/concepts.md](explaining_the_project/concepts.md) — what OpenHIS **is**, what it is **not**, and the three governing principles; read this before writing any code.
- [explaining_the_project/architecture.md](explaining_the_project/architecture.md) — the platform deep-dive: services, event bus, FHIR spine, auth flow.

## Contracts & extension guides

- [explaining_the_project/adding-a-module.md](explaining_the_project/adding-a-module.md) — how to integrate a new clinical application or native service.
- [explaining_the_project/adapter-contract.md](explaining_the_project/adapter-contract.md) — what every integration-hub adapter must implement.
- [explaining_the_project/service-contract.md](explaining_the_project/service-contract.md) — what every native FastAPI service must satisfy to be managed by OPM.
- [explaining_the_project/profile-contract.md](explaining_the_project/profile-contract.md) — what a Docker Compose profile overlay must provide.
- [explaining_the_project/profiles.md](explaining_the_project/profiles.md) — the deployment profile system and what each profile ships.

## Operations & security

- [explaining_the_project/security.md](explaining_the_project/security.md) — hardening checklist before running with real patient data.
- [explaining_the_project/data-retention.md](explaining_the_project/data-retention.md) — retention and archival policies per data store.
- [explaining_the_project/adding-keycloak-sso.md](explaining_the_project/adding-keycloak-sso.md) — wiring Keycloak OIDC SSO into a third-party module, with the non-obvious pitfalls.

## Architectural Decision Records (ADRs)

- [adr/0001-redis-streams-as-event-bus.md](adr/0001-redis-streams-as-event-bus.md) — why Redis Streams (not Kafka/RabbitMQ) is the event bus.
- [adr/0002-integration-hub-as-fhir-adapter.md](adr/0002-integration-hub-as-fhir-adapter.md) — why all cross-system calls live in one FHIR R4 adapter hub.
- [adr/0003-mpi-as-identity-spine.md](adr/0003-mpi-as-identity-spine.md) — why the MPI is the single patient-identity authority.
- [adr/0005-bus-dead-letter-semantics.md](adr/0005-bus-dead-letter-semantics.md) — ack-on-success, XAUTOCLAIM retry, and the dead-letter queue.
- [adr/0006-mpi-matcher-threshold.md](adr/0006-mpi-matcher-threshold.md) — matcher threshold 0.75, diacritics handling, phonetic floor.

## Verification & validation

- [verification_and_validation/v-and-v-scenario.md](verification_and_validation/v-and-v-scenario.md) — the narrative V&V scenarios; executable mirror lives in `tests/e2e/`.
- [verification_and_validation/demo-walkthrough.md](verification_and_validation/demo-walkthrough.md) — step-by-step manual verification of every integration path.

## Benchmarks

- [benchmarks/mpi-matching.md](benchmarks/mpi-matching.md) — accuracy benchmark of the MPI patient matcher over a labelled synthetic corpus.

## Planning & roadmap

- [ROADMAP.md](ROADMAP.md) — where the project is going and how it differentiates (in French).
- [task_planning/REMEDIATION_PLAN.md](task_planning/REMEDIATION_PLAN.md) — the audit remediation plan, one PR-sized task at a time.
- [task_planning/4_TODO_list.md](task_planning/4_TODO_list.md) — the objective-by-objective TODO list.
- [task_planning/test-defect-report-2026-04-14.md](task_planning/test-defect-report-2026-04-14.md) — live defect report backing the `xfail` markers in `tests/e2e/`.
- [task_planning/archive/](task_planning/archive/) — superseded point-in-time plans, kept for historical context.

## Reviews

- [reviews/](reviews/) — review-session artifacts (e.g. [PROMPT_DEF-010_DEF-006_FIX.review.md](reviews/PROMPT_DEF-010_DEF-006_FIX.review.md)).

## Contributor guidelines

- [guidelines_for_contributors/CONTRIBUTING.md](guidelines_for_contributors/CONTRIBUTING.md) — zero to merged pull request.
- [guidelines_for_contributors/SECURITY.md](guidelines_for_contributors/SECURITY.md) — vulnerability reporting and supported versions.
- [guidelines_for_contributors/CODE_OF_CONDUCT.md](guidelines_for_contributors/CODE_OF_CONDUCT.md) — the Contributor Covenant.
