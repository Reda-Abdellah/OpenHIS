# PLAN 2026-07 — Salvage platform work & first public release

> Task **definitions** only — statuses live in [INDEX.md](INDEX.md).
> Epics: Phase S = **EP-01**, Phase V = **EP-02**, Phase R = **EP-03**.

**Context.** The local branch `study/biogml-advisor-cds-integration` (never
pushed — keep it that way) mixes two things: the BioGML-Advisor CDS
integration, which is **company-proprietary and out of scope** for the
open-source project, and ~2 months of pure platform work (June 2026
remediation wave: security T-01…T-16, bus DLQ, SDK metrics, backups, MPI
matcher/benchmark/FHIR facade, OPM packaging, docs). `master` has none of it:
6 defects are still open (32 `xfail` markers in `tests/e2e`) and the
REMEDIATION_PLAN tracker is entirely unticked.

**Goal.** Extract every non-CDS improvement from the study branch into
`master` in reviewable thematic PRs, validate live, then cut the first
public release. No CDS/BioGML artefact may reach `master`.

**Method (all S-tasks).** Work file-by-file from the triage below:
- Clean files: `git checkout study/biogml-advisor-cds-integration -- <paths>`
- Mixed files: apply hunks selectively from
  `git diff master..study/biogml-advisor-cds-integration -- <file>` — never
  check out a mixed file wholesale.
- After each wave: grep the diff for `biogml|cds|mirth|fhir-buffer` (case-
  insensitive) — zero hits allowed; `pytest tests/unit tests/integration` green.

**Permanently excluded (CDS-only, ~44 files):**
`services/biogml-advisor/`, `compose/profiles/cds.yml`,
`docs/adr/0004-biogml-advisor-cds-integration.md`,
`docs/explaining_the_project/clinical-decision-support.md`,
Mirth Connect + `fhir-buffer`/`fhir-local` additions in
`compose/profiles/laboratory.yml` and `infra/openelis/`,
`infra/openelis/init/30-cds-loinc-map.sql`, `scripts/apply_oe_loinc_map.sh`,
the hub's `/api/cds/*` routers and pre-routing hook, the hl7 critical-value
chain (ORU^R30 store/ack, inbox closure), and every `cds.*` bus event.

---

## Phase S — Salvage (EP-01 — one PR per task)

### S-00: IP hygiene — snapshot and quarantine the study branch
**Priority:** P0 · **Depends on:** — · **Branch:** n/a (local ops only)
**Plan:** `git bundle create ~/openhis-cds-study.bundle study/biogml-advisor-cds-integration`;
store the bundle outside the repo; confirm `git branch -r` shows no remote
copy. Do NOT delete the branch until S-08 is merged.
**Acceptance:** bundle restores the branch in a scratch clone; no remote ref exists.

### S-01: SDK — bus dead-letter semantics + Prometheus metrics
**Priority:** P0 · **Depends on:** S-00 · **Branch:** `feat/S-01-sdk-dlq-metrics`
**Files:** `libs/openhis_sdk/` (bus.py, metrics.py, middleware.py, auth.py DEV_MODE guard, pyproject.toml), `tests/unit/sdk/`, `docs/adr/0005-bus-dead-letter-semantics.md`, `infra/prometheus/alerts-example.yml`
**Plan:** import SDK changes: ack-after-success + XAUTOCLAIM retry + bounded
`openhis:events:dlq` (ADR-0005); `openhis_sdk.metrics` (MetricsMiddleware,
`/metrics` router, DLQ-depth gauge); unified `MAXLEN`; `DEV_MODE` refuses to
boot unless `ENV=development`. Strip the `openhis:cds:dlq` references from
metrics/alerts.
**Acceptance:** `pytest tests/unit/sdk -q` green; imports T-07 (tick tracker).

### S-02: Infra security hardening (T-01…T-10)
**Priority:** P0 · **Depends on:** S-01 · **Branch:** `security/S-02-infra-hardening`
**Files:** `infra/nginx/` (njs RS256 guard, nginx.conf.j2), `infra/keycloak/openhis-realm.json.j2`, `infra/orthanc/` (production config, plugin.py token auth), `infra/odoo/`, `infra/openmrs/oauth2.properties.j2`, `compose/base.yml` (credential env-vars, Redis AUTH, socket-proxy, MLLP internal), `compose/overrides/{production,mllp-public}.yml`, `platform/opm.py` + `platform/infra_render.py` (secret generation, realm templating), `scripts/gen_dev_certs.sh`, `.env.example`
**Plan:** apply the branch's hardening hunks, minus anything referencing the
cds profile or Mirth. `compose/profiles/laboratory.yml`: keep credential
externalization hunks, drop the Mirth/fhir-buffer services.
**Acceptance:** `opm init` generates strong secrets; stack boots with `emr`
profile; ticks T-01, T-04, T-06…T-10 (partial mapping noted in PR body).

### S-03: Service lockdowns + defect fixes (DEF-001/002/007/008/010)
**Priority:** P0 · **Depends on:** S-01 · **Branch:** `fix/S-03-lockdowns-defects`
**Files:** `services/{admin,mpi,ris,ai-controller,analytics,simulator,hl7,integration-hub}/` (role gating, audit rows, `mpi/bus.py` publish-after-commit, unauthenticated upstream health probes, HL7 PID persistence, hub `patient.synced` consumer), matching `tests/unit/`, `tests/e2e/` xfail updates, `tests/conftest.py` + integration conftest (event-loop fix)
**Plan:** import service hunks, excluding the hub CDS hook/routers and the
hl7 critical-value consumer paths. Update `test-defect-report-2026-04-14.md`
statuses to "FIXED IN CODE — pending live e2e".
**Acceptance:** full `make test` green in ONE invocation (event-loop fix
proves itself); ticks T-02, T-03, T-05, T-11.

### S-04: MPI — matcher tuning, benchmark floors, FHIR facade
**Priority:** P1 · **Depends on:** S-03 · **Branch:** `feat/S-04-mpi-upgrades`
**Files:** `services/mpi/{matcher.py,routers/fhir.py,routers/matching.py}`, `tests/benchmarks/`, `docs/benchmarks/mpi-matching.md`, `docs/adr/0006-mpi-matcher-threshold.md`
**Plan:** threshold 0.75 + diacritics + Metaphone (ADR-0006); precision/recall
regression floors; PDQm search + `$ihe-pix` facade.
**Acceptance:** `pytest tests/benchmarks -q` green; ticks T-16.

### S-05: Auth harness + CI pipeline
**Priority:** P1 · **Depends on:** S-03 · **Branch:** `test/S-05-auth-harness-ci`
**Files:** `tests/auth/`, `.github/workflows/ci.yml`, `Makefile` (test targets)
**Plan:** import the deny-by-default harness (currently an empty dir on
master) and the CI wiring (auth + benchmarks on PR, e2e job on merge).
Strip any cds-profile steps from the workflow.
**Acceptance:** `pytest tests/auth -q` green locally; CI passes on the PR.

### S-06: Backup & restore tooling
**Priority:** P1 · **Depends on:** S-02 · **Branch:** `feat/S-06-backup-restore`
**Files:** `scripts/{backup.sh,restore.sh,README.md}`, `Makefile`
**Plan:** profile-aware dump/restore with `--dry-run`, sha256 manifest,
compose-driven completeness self-test. Remove cds-profile entries from the
profile map.
**Acceptance:** `make backup` then `make restore --dry-run` succeed on a
running base stack.

### S-07: OPM packaging + public docs
**Priority:** P2 · **Depends on:** S-02 · **Branch:** `docs/S-07-opm-packaging-docs`
**Files:** `platform/{pyproject.toml,README.md,profile_engine.py}`, `docs/quickstart.md`, `docs/ROADMAP.md`, `docs/design/{multi-site,profile-marketplace,kafka-migration}.md`, `docs/explaining_the_project/*.md`, `docs/guidelines_for_contributors/*`, `docs/README.md`
**Plan:** PyPI-ready `openhis-opm` with `opm` console script; French
quickstart; ROADMAP **rewritten** to remove the CDS differentiator sections
(the BioGML rows/paragraphs must not be imported).
**Acceptance:** `pip install -e platform && opm --version` works; grep gate
passes on all imported docs.

### S-08: Root-file reconciliation + branch retirement
**Priority:** P2 · **Depends on:** S-01…S-07 · **Branch:** `chore/S-08-root-reconciliation`
**Files:** `CHANGELOG.md`, `CLAUDE.md`, `README.md`, `.gitignore`, `.env.example`, remaining `openhis.service.json` manifests
**Plan:** merge the branch's Unreleased CHANGELOG entries minus every CDS
line; align CLAUDE.md with what actually landed; final repo-wide grep gate.
Then delete the local study branch (bundle from S-00 is the archive).
**Acceptance:** `git grep -iE "biogml|/cds|mirth" -- ':!docs/task_planning'`
returns nothing unexpected; `make test` green.

## Phase V — Live validation (EP-02)

### V-01: Full-stack e2e pass, close the defects
**Priority:** P0 · **Depends on:** S-03, S-05 · **Branch:** `test/V-01-live-e2e`
**Plan:** `make up && make health && make e2e`. Remove every `xfail` that
XPASSes; mark the corresponding DEF-NNN **CLOSED (validated live <date>)**
in the defect registry; tick the REMEDIATION_PLAN tracker for everything
imported.
**Acceptance:** `make e2e` reports 0 FAILED, 0 XPASSED; defect registry shows
0 open DEF.

## Phase R — First public release (EP-03)

### R-01: Tag `v0.1.0-alpha`
**Depends on:** V-01 — CHANGELOG release section, annotated tag, GitHub release notes.
### R-02: Publish Docker images to GHCR on tag (via `release.yml`).
### R-03: Publish `openhis-opm` to PyPI.
### R-04: Record the quickstart demo (VPS → running stack < 1 h) for the README.

---

*Statuses for all tasks above: see the board in [INDEX.md](INDEX.md).*
