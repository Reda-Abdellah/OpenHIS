# INDEX — OpenHIS task board

> Single source of truth for **status**. Task definitions live in the plan
> files; conventions in [README.md](README.md). Update this file in the same
> PR as the work. Last updated: **2026-07-09** (S-00 done — EP-01 at 1/9).

---

## Epics

| ID | Epic | Outcome | Plan | Status |
|---|---|---|---|---|
| EP-01 | **Salvage platform work from study branch** | Every non-CDS improvement of the June-2026 wave merged to `master`; zero BioGML/CDS artefact imported; study branch retired | [PLAN-2026-07](PLAN-2026-07-salvage-and-release.md) Phase S | `TODO` |
| EP-02 | **Live validation — zero open defects** | Full-stack e2e green, all `xfail` markers for fixed defects removed, defect registry at 0 open | [PLAN-2026-07](PLAN-2026-07-salvage-and-release.md) Phase V | `TODO` |
| EP-03 | **First public release `v0.1.0-alpha`** | Tag + GHCR images + `openhis-opm` on PyPI + quickstart demo | [PLAN-2026-07](PLAN-2026-07-salvage-and-release.md) Phase R | `TODO` |
| EP-04 | **Audit remediation backlog** | Remaining T-tasks not covered by the salvage (T-12…T-15, T-17…T-35) triaged and executed | [REMEDIATION_PLAN.md](REMEDIATION_PLAN.md) | `TODO` — schedule after EP-01, many T-tasks land via S-01…S-05 |
| EP-05 | **Product backlog (OBJ 1–8)** | Long-term objectives: compliance (OBJ 5), open-source readiness (OBJ 6), observability (OBJ 8)… | [4_TODO_list.md](4_TODO_list.md) | `TODO` — unscheduled reservoir |

---

## Board

### 🔵 To do

| Task | Title | Epic | Prio | Depends on |
|---|---|---|---|---|
| S-01 | SDK — bus DLQ + Prometheus metrics | EP-01 | P0 | S-00 |
| S-02 | Infra security hardening (T-01…T-10) | EP-01 | P0 | S-01 |
| S-03 | Service lockdowns + defect fixes | EP-01 | P0 | S-01 |
| S-04 | MPI — matcher, benchmark, FHIR facade | EP-01 | P1 | S-03 |
| S-05 | Auth harness + CI pipeline | EP-01 | P1 | S-03 |
| S-06 | Backup & restore tooling | EP-01 | P1 | S-02 |
| S-07 | OPM packaging + public docs | EP-01 | P2 | S-02 |
| S-08 | Root reconciliation + branch retirement | EP-01 | P2 | S-01…S-07 |
| V-01 | Full-stack e2e pass, close the defects | EP-02 | P0 | S-03, S-05 |
| R-01 | Tag `v0.1.0-alpha` | EP-03 | P1 | V-01 |
| R-02 | Publish Docker images to GHCR | EP-03 | P1 | R-01 |
| R-03 | Publish `openhis-opm` to PyPI | EP-03 | P1 | R-01 |
| R-04 | Record quickstart demo | EP-03 | P2 | R-01 |

### 🟡 In progress

| Task | Title | Epic | Branch | Since |
|---|---|---|---|---|
| — | | | | |

### 🔴 Blocked

| Task | Title | Epic | Blocked by |
|---|---|---|---|
| — | | | |

### 🟢 Done

| Task | Title | Epic | Date | PR |
|---|---|---|---|---|
| S-00 | IP hygiene — bundle + quarantine study branch (`~/openhis-cds-study.bundle`, restore verified, no remote ref) | EP-01 | 2026-07-09 | local ops |

---

## Defects (summary — forensics in [test-defect-report-2026-04-14.md](test-defect-report-2026-04-14.md))

| ID | Summary | Status on `master` | Fix arrives via |
|---|---|---|---|
| DEF-001 | Adapter health checks require a Keycloak token | `OPEN` | S-03 |
| DEF-002 | Admin registry mutations not audited | `OPEN` | S-03 |
| DEF-003 | MPI unit tests require live PostgreSQL | `OPEN` | S-05 (CI Postgres sidecar) |
| DEF-004 | MPI `find_candidates` self-filters without ids | `OPEN` | S-04 |
| DEF-006 | OpenELIS 302 redirect loop | `RESOLVED 2026-04-19` | — |
| DEF-007 | Analytics refuses every call: "KEYCLOAK_URL missing" | `OPEN` | S-03 |
| DEF-008 | HL7 outbound: patient identifiers not persisted | `OPEN` | S-03 |
| DEF-010 | Hub has no `patient.synced` consumer → MPI patients not pushed to OpenELIS | `OPEN` | S-03 |

`OPEN` = broken on `master` today (e2e `xfail` markers reference these IDs).
Closing a defect requires V-01's live validation, not just merged code.

---

## Archive

| File | What it was | Superseded by |
|---|---|---|
| [archive/1_Cohesion_Transformation_Plan.md](archive/1_Cohesion_Transformation_Plan.md) | Initial cohesion/transformation plan | REMEDIATION_PLAN.md |
| [archive/2_Plan_enhance_openhis.md](archive/2_Plan_enhance_openhis.md) | Early enhancement plan | 4_TODO_list.md |
| [archive/3_Enhance_structure_for_clarity.md](archive/3_Enhance_structure_for_clarity.md) | Repo restructuring plan (executed) | — |
| [archive/5_test_coverage.md](archive/5_test_coverage.md) | Test-coverage push (executed) | tests/ layout + CI |
| [archive/6_uniform_auth.md](archive/6_uniform_auth.md) | Auth unification plan (executed) | `openhis_sdk.auth` |
| [archive/FEATURES.md](archive/FEATURES.md) | Feature inventory snapshot | docs/explaining_the_project/ |
