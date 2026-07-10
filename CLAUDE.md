# CLAUDE.md — OpenHIS

Behavioral guardrails + project context for OpenHIS. Concise by design —
deep docs are linked, not inlined; when this file and a linked doc
disagree, fix the drift instead of guessing. Use judgment on trivial tasks.

## 0. How to work here

**Think before coding.** State assumptions explicitly; if uncertain, ask.
If multiple interpretations exist, surface them — don't pick silently.
If a simpler approach exists, say so.

**Simplicity & surgical changes.** Minimum code that solves the problem,
nothing speculative. Touch only what the task requires; match existing
style; don't refactor what isn't broken. Every changed line should trace
to the request. Remove only the orphans *your* change created.

**Goal-driven.** Turn tasks into verifiable goals ("fix bug" → "write a
test that reproduces it, then make it pass"). Unit/integration tests mock
at the HTTP boundary (`respx`) or use in-memory impls (`fakeredis`) — no
live containers, no network.

**Session start.** Read the board:
[docs/task_planning/INDEX.md](docs/task_planning/INDEX.md) — single source
of truth for statuses (epics, tasks, `DEF-NNN` defects). Before working a
topic, check it isn't already tracked. Flip statuses in the same PR as the
work; conventions in [docs/task_planning/README.md](docs/task_planning/README.md).

**Platform boundary (this project's hard rule).** OpenHIS is the platform
— identity (MPI), flows (FHIR R4 + Redis Streams), deployment (profiles +
OPM), operations (SSO, audit, health). Clinical domain logic belongs to
the integrated applications. **Test:** if the change encodes clinical
knowledge (encounter forms, lab panels, billing rules, decision support)
→ it belongs in OpenMRS/OpenELIS/Odoo or an external module, not here. If
it adds a service-to-service HTTP call → rethink as adapter + bus event.
Full thesis: @docs/explaining_the_project/concepts.md

## 1. What this is

An **open-source, profile-driven Health Information Platform**: it
orchestrates best-of-breed clinical systems (OpenMRS, OpenELIS, Odoo,
Orthanc, OHIF) instead of replacing them. Integration spine = **FHIR R4**
(data contract) + **Redis Streams** (event bus); Keycloak for all auth.

Stack: Python 3.11 · FastAPI · PostgreSQL · Redis Streams · Nginx ·
Docker Compose profiles · shared SDK (`libs/openhis_sdk`) · OPM CLI
(`platform/opm.py`) · pytest/respx/fakeredis.

Architecture: @docs/explaining_the_project/architecture.md · ADRs: @docs/adr/

## 2. Layout

```
compose/          base.yml + profiles/ (emr laboratory imaging erp analytics) + overrides/
libs/openhis_sdk/ Shared SDK — canonical auth, bus, logging, retry, metrics
platform/         OPM CLI + profile/nginx/infra engines (pip-installable)
services/         Native FastAPI services: admin mpi integration-hub hl7 ris
                  ai-controller analytics patient-portal simulator
                  _legacy/ ⚠ FROZEN — do NOT extend
infra/            Third-party configs (nginx, keycloak, orthanc, openelis, odoo…)
pipelines/        AI pipeline workers (poc-ct, poc-xray)
tests/            unit/ integration/ auth/ benchmarks/ e2e/ smoke/
docs/             README.md = table of contents; task_planning/ = the board
```

Source of truth for ports, bus topics and env vars: each service's
`openhis.service.json` manifest. For profile composition and RAM budgets:
`compose/profiles/*.yml` (`x-openhis` block) and
@docs/explaining_the_project/profiles.md. Don't trust prose copies —
including in this file.

## 3. Key design rules

- **SDK only** for auth/logging/bus/retry/metrics: `from openhis_sdk.auth
  import require_token, require_roles` (or `JWTMiddleware`), `from
  openhis_sdk.logging import configure`, `from openhis_sdk.bus import
  publish, BusConsumer`, `from openhis_sdk.retry import with_retry`.
  **Never** create per-service `jwt_auth.py` / `log_config.py` with real
  logic — existing copies are re-export shims; CI rejects new ones.
- **Adapter rule:** no service calls another system's API directly — all
  cross-system flows live in `integration-hub/app/services/<app>.py`
  (@docs/explaining_the_project/adapter-contract.md). Native services
  needing an upstream read go through the hub's audited `/api/context/*`
  surface (`internal-sync` machine role).
- **Every cross-system write → hub audit log entry.** Every successful
  sync → bus event. New flow = new event, not a new HTTP call.
- Bus delivery semantics (ack-on-success, XAUTOCLAIM retry, bounded
  `openhis:events:dlq`): ADR 0005. Consumers must be idempotent.
- Every service: `main.py` with `lifespan` (never `@app.on_event`),
  manifest, Dockerfile, startup env guard (`sys.exit` on missing
  required vars), tests. Scaffold: `python platform/opm.py add-service`.
  Contract: @docs/explaining_the_project/service-contract.md
- Python: type hints on public functions; Pydantic v2; f-strings;
  `datetime.now(timezone.utc)` — never `utcnow()`; no bare `except:`.
- `infra/nginx/nginx.conf` is **generated** from `nginx.conf.j2` — never
  hand-edit; regenerate via OPM/`platform/nginx_gen.py`.

## 4. Commands

```bash
# Setup (venv lives at venv_openhis/)
pip install -e libs/openhis_sdk -e platform && pip install -e 'libs/openhis_sdk[dev]'
cp .env.example .env && python platform/opm.py init   # renders realm + secrets

# Stack
OPENHIS_PROFILES=emr,laboratory make up   # opm init/demo-render REQUIRED first
make health
python platform/opm.py {init,enable,disable,status,add-service,demo-render}

# Tests — no Docker needed
pytest tests/unit -q --tb=short            # ~30 s, run on every change
pytest tests/integration -q --tb=short     # respx mocks
pytest tests/auth -q                       # deny-by-default 401/403/2xx harness
pytest tests/benchmarks -q                 # MPI matching precision/recall floors
make test                                  # unit + integration in one invocation

# Tests — live stack required
make e2e                                   # V&V scenarios (~40 s once stack is up)
pytest tests/unit/<service> -x -q --tb=short   # targeted, during development
```

## 5. Verification ladder — before declaring work done

Scale the sweep to the blast radius; the pain this prevents is fixing one
service and silently breaking another.

1. Targeted: `pytest tests/unit/<service> -x -q --tb=short`
2. Sweep: `make test`
3. **Live e2e** whenever the change could affect cross-service behaviour
   (auth, bus events, FHIR/HL7 flow, adapter contracts, Keycloak clients,
   nginx routes, manifests): `make up && make health && make e2e`.
   Reading the summary: **FAILED** = regression, bisect before moving on.
   **XPASSED** = you fixed a known defect — remove that test's
   `xfail(DEF-NNN)` marker and update the defect registry. New cross-
   service flow → add a scenario (narrative spec
   @docs/verification_and_validation/v-and-v-scenario.md, executable
   mirror `tests/e2e/` — keep in sync).
4. **Never** bypass the suite (`--no-verify`, deleting/commenting
   assertions). A blocking test is either a root cause to diagnose or an
   `xfail` with a `DEF-NNN` entry in the registry.
5. Rebuild images after code changes before live validation: `docker
   compose … build` **from the repo root** (build contexts use `${PWD}`)
   — `up -d` alone never rebuilds. Restart nginx after recreating
   services (it resolves upstream IPs at startup).

## 6. Git & PR

- Default branch `master`; branches `feat/…` `fix/…` `docs/…`
  `security/…`; Conventional Commits (`type(scope): summary`).
- PR checklist: `make test` green · e2e run if cross-service (no new
  FAILED, no surviving XPASSED) · manifest + `.env.example` updated for
  any new env var/port/topic · `CHANGELOG.md` under `Unreleased` · no new
  auth/logging copies outside `libs/` · no hardcoded secrets · board
  (`INDEX.md`) statuses flipped in the same PR.

## 7. Gotchas

- **Fresh clones must run `opm init` (or `opm demo-render`) before
  `make up`** — the rendered Keycloak realm and OpenELIS/OpenMRS property
  files are gitignored (they carry secrets); `make up` refuses to start
  without them. Keycloak imports the realm at first boot only.
- **Boot order matters:** if nginx is down when OpenMRS/OpenELIS boot,
  their Spring contexts die on the one-shot OIDC discovery call and every
  request 302-loops (DEF-006 fingerprint) — restart them after nginx is
  healthy.
- **Every `*-sa` service needs `KEYCLOAK_AUDIENCE=openhis-platform`** in
  compose: tokens carry the platform audience; the SDK otherwise expects
  `aud=<client_id>` and rejects all valid tokens.
- **Never name a module `token.py`** (or any stdlib name) in a service —
  it shadows the stdlib via `tokenize` and poisons imports; use
  `sa_token.py`.
- Keycloak runs `start-dev` by default — production uses
  `compose/overrides/production.yml`. `DEV_MODE=true` requires
  `ENV=development` (SDK exits otherwise).
- MLLP 2575 is internal-only (re-expose via `overrides/mllp-public.yml`).
  `/metrics` is JWT-exempt but nginx blocks it externally — scrape from
  inside the compose network.
- Integration tests mock with `respx` at the **HTTP boundary only** —
  never at the adapter layer.
- redis-py is pinned `<6` in the SDK (≥6 turns blocking stream reads into
  hard socket timeouts) — don't "upgrade" it in a service's requirements.

## Notes

- `services/_legacy/` (ehr, lis, pharmacy) is frozen — replaced by
  OpenMRS/OpenELIS/Odoo; do not extend.
- Clinical decision support is **out of scope** for this repository —
  it is an external module territory (platform boundary test above).
- Known open defects (currently DEF-011: OpenMRS machine-token access
  under SSO; DEF-012: OpenELIS FHIR façade needs a backing FHIR store)
  live in the registry — check the board before debugging those areas.

## References

Concepts (start here): @docs/explaining_the_project/concepts.md ·
Architecture: @docs/explaining_the_project/architecture.md ·
Adding a module: @docs/explaining_the_project/adding-a-module.md ·
Security: @docs/guidelines_for_contributors/SECURITY.md ·
Contributing: @docs/guidelines_for_contributors/CONTRIBUTING.md ·
Quickstart (FR): @docs/quickstart.md · Roadmap (FR): @docs/ROADMAP.md
