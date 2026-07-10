# INDEX — OpenHIS task board

> Single source of truth for **status**. Task definitions live in the plan
> files; conventions in [README.md](README.md). Update this file in the same
> PR as the work. Last updated: **2026-07-10** (R-01 done — v0.6.0-alpha tagged locally; push master + tag to fire GHCR/PyPI/Release).

---

## Epics

| ID | Epic | Outcome | Plan | Status |
|---|---|---|---|---|
| EP-01 | **Salvage platform work from study branch** | Every non-CDS improvement of the June-2026 wave merged to `master`; zero BioGML/CDS artefact imported; study branch retired | [PLAN-2026-07](PLAN-2026-07-salvage-and-release.md) Phase S | `DONE 2026-07-09` (9/9) |
| EP-02 | **Live validation — zero open defects** | Full-stack e2e green, all `xfail` markers for fixed defects removed, defect registry at 0 open | [PLAN-2026-07](PLAN-2026-07-salvage-and-release.md) Phase V | `DONE 2026-07-10` — **0 open defects**; remaining e2e xfails are seed gaps, not defects |
| EP-03 | **First public release `v0.6.0-alpha`** | Tag + GHCR images + `openhis-opm` on PyPI + quickstart demo | [PLAN-2026-07](PLAN-2026-07-salvage-and-release.md) Phase R | `WIP` — tag & pipeline ready; fires on push |
| EP-04 | **Audit remediation backlog** | Remaining T-tasks not covered by the salvage (T-12…T-15, T-17…T-35) triaged and executed | [REMEDIATION_PLAN.md](REMEDIATION_PLAN.md) | `TODO` — schedule after EP-01, many T-tasks land via S-01…S-05 |
| EP-05 | **Product backlog (OBJ 1–8)** | Long-term objectives: compliance (OBJ 5), open-source readiness (OBJ 6), observability (OBJ 8)… | [4_TODO_list.md](4_TODO_list.md) | `TODO` — unscheduled reservoir |

---

## Board

### 🔵 To do

| Task | Title | Epic | Prio | Depends on |
|---|---|---|---|---|
| R-02 | Publish Docker images to GHCR — release.yml fires on tag push (`git push origin v0.6.0-alpha`) | EP-03 | P1 | tag push |
| R-03 | Publish `openhis-opm` to PyPI — job ready (Trusted Publishing); one-time setup on pypi.org: project `openhis-opm`, workflow `release.yml`, environment `pypi` (+ create the GitHub environment) | EP-03 | P1 | tag push |
| R-04 | Record quickstart demo (human: film `opm init` → `make up` → portal tour, docs/quickstart.md as script) | EP-03 | P2 | — |

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
| S-01 | SDK — bus DLQ + Prometheus metrics (imports T-07, part of T-04; 364 tests green) | EP-01 | 2026-07-09 | merge `d8d42b4` |
| S-02 | Infra security hardening — imports T-01, T-04, T-06…T-10 (realm/extra.properties templated, njs RS256, socket-proxy, MLLP internal; compose renders all profiles) | EP-01 | 2026-07-09 | merge `97d3ada` |
| S-03 | Service lockdowns + defect fixes — DEF-001/002/007/008/010 fixed in code (pending V-01); T-02/03/05/06/16; MPI matcher + FHIR facade; auth harness; hub `/api/context` surface; 631 tests green | EP-01 | 2026-07-09 | merge `521bc22` |
| S-04 | MPI benchmark suite (precision/recall floors) + ADR-0006 docs + DEF-004 fix (xfail promoted) | EP-01 | 2026-07-09 | merge `665bd41` |
| S-05 | CI wiring — auth harness + benchmarks on PR, MPI Postgres sidecar (DEF-003), e2e job with demo-render step | EP-01 | 2026-07-09 | merge `aaec13a` |
| S-06 | Backup & restore tooling (`make backup`/`restore`, completeness self-test, dry-run exercised) | EP-01 | 2026-07-09 | merge `b09b3ac` |
| S-07 | OPM PyPI packaging (`opm --version` OK) + quickstart + ROADMAP rewritten without CDS + design notes | EP-01 | 2026-07-09 | merge `3bafcc2` |
| S-08 | Root reconciliation (README/CLAUDE.md stripped), repo-wide CDS gate clean, study branch deleted (bundle kept) — 738 tests green, 0 xfail | EP-01 | 2026-07-09 | merge `d1dfef7`+ |
| V-01 | Live e2e validation — **64 passed, 0 failed, 5 xfail** on the full clinical stack; DEF-001/002/007/008 closed live, DEF-010 code-complete (hub consumer shipped); found & fixed: compose audience vars, nginx `$remote_user` crash, `token.py` stdlib shadowing (T-17), redis-py ≥6 timeout, analytics API↔V&V drift; opened DEF-011/DEF-012 | EP-02 | 2026-07-10 | see merge |
| D-02 | OpenELIS backing FHIR store (`oe-fhir-store` HAPI, laboratory profile) — DEF-012 **and** DEF-010 closed live; e2e S1.6 passes hard (65 passed, 0 failed); also fixed empty-body-201 parse + master→oe dedup map in the hub | EP-02 | 2026-07-10 | see merge |
| D-01 | DEF-011 closed — oauth2login's `OAuth2ServiceAccountFilter` accepts bearer JWTs once the matching OpenMRS user exists: new `openmrs-init` one-shot provisions `service-account-integration-hub-sa` (SQL, idempotent); lab-result reads moved to the OE FHIR store; e2e S2.4 & S2.6 pass live end-to-end — **67 passed, 0 failed, 2 seed-gap xfail** | EP-02 | 2026-07-10 | see merge |
| R-01 | `v0.6.0-alpha` cut — CHANGELOG frozen with release summary, annotated tag created locally, opm 0.6.0-alpha.1, release.yml upgraded (section-only notes + PyPI Trusted Publishing job); fires on `git push origin v0.6.0-alpha` | EP-03 | 2026-07-10 | merge `2e1f65a` |

---

## Defects (summary — forensics in [test-defect-report-2026-04-14.md](test-defect-report-2026-04-14.md))

| ID | Summary | Status on `master` | Fix arrives via |
|---|---|---|---|
| DEF-001 | Adapter health checks require a Keycloak token | `CLOSED 2026-07-10` — validated live (V-01) | S-03 |
| DEF-002 | Admin registry mutations not audited | `CLOSED 2026-07-10` — validated live (V-01) | S-03 |
| DEF-003 | MPI unit tests require live PostgreSQL | `FIXED IN CI 2026-07-09` — Postgres sidecar + anti-silent-skip guard | S-05 |
| DEF-004 | MPI `find_candidates` self-filters without ids | `FIXED 2026-07-09` — guard `pid is not None`, xfail promoted | S-04 |
| DEF-006 | OpenELIS 302 redirect loop | `RESOLVED 2026-04-19` | — |
| DEF-007 | Analytics refuses every call: "KEYCLOAK_URL missing" | `CLOSED 2026-07-10` — validated live (V-01) | S-03 |
| DEF-008 | HL7 outbound: patient identifiers not persisted | `CLOSED 2026-07-10` — validated live (V-01) | S-03 |
| DEF-010 | Hub has no `patient.synced` consumer → MPI patients not pushed to OpenELIS | `CLOSED 2026-07-10` — validated live, e2e S1.6 asserts the full chain | D-02 |
| DEF-011 | hub↔OpenMRS FHIR sync rejected under oauth2login SSO (302 → login for bearer AND Basic) | `CLOSED 2026-07-10` — SA user provisioned by `openmrs-init`; bearer path validated live | D-01 |
| DEF-012 | OpenELIS FHIR façade 500s on every search/write without a backing FHIR store | `CLOSED 2026-07-10` — `oe-fhir-store` HAPI shipped in the laboratory profile | D-02 |

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
