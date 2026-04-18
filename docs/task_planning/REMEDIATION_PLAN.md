# OpenHIS — Audit Remediation Plan

This plan turns the audit findings (78 items across 5 reports) into 35 executable tasks. Each task is scoped to a single PR and includes files to touch, concrete edits, and a verifiable acceptance check.

Designed to be handed to Claude Code in an IDE, one task at a time.

---

## How to use this plan

**Per-task workflow:**

1. Start a fresh branch: `git checkout -b <branch-name-from-task>` off `main`.
2. Paste the task block into Claude Code. Ask it to follow the "Plan" section and verify the "Acceptance criteria".
3. Run the specified verification commands locally.
4. Open a PR with the suggested title. Reference the finding numbers (F#NN) in the PR body.
5. Check off the task in the tracker at the bottom of this file.

**Don't skip ahead.** The phases are ordered by dependency: Phase 1 fixes things that actively bleed; Phase 2 builds the detection infrastructure so future regressions get caught; Phases 3–5 are correctness, hardening, and cleanup. Several Phase 3 tasks assume the Phase 2 test harness exists.

**Branching:** one branch per task, named as indicated (`fix/T-XX-short-description`). One PR per branch. Conventional commit prefix: `fix`, `feat`, `refactor`, `test`, `security`, `chore`, `docs`.

**Scope discipline:** if a task says "do X", do X and nothing else, even if you see an adjacent mess. Adjacent messes have their own tasks. Keeping diffs small keeps review fast and revert safe.

**Finding references:** `F#NN` = finding number from the original audit. These are kept for traceability — each PR body should list the F-numbers it closes.

---

## Conventions

- Python: 3.11. Use `datetime.now(timezone.utc)` — never `datetime.utcnow()`.
- All auth uses the shared SDK: `from openhis_sdk.auth import JWTMiddleware, require_token, require_roles`.
- All logging uses the shared SDK: `from openhis_sdk.logging import configure`.
- Bus publish/consume uses the shared SDK: `from openhis_sdk.bus import publish_event, BusConsumer`.
- Tests live in `tests/unit/<service>/` (unit) or `tests/integration/` (integration). The pattern `tests/<service>/` is wrong and several references to it need fixing.
- PR checklist: `pytest tests/unit tests/integration` passes; `CHANGELOG.md` updated under `## Unreleased`; `openhis.service.json` updated if ports/paths/bus topics changed; no new `jwt_auth.py` / `log_config.py` with real logic outside `libs/`.

---

## Phase 1 — Stop the bleeding (P0, security-critical)

These are exploitable on a reachable deployment. Do them first, in order listed. None of them depends on any other phase's work.

---

### T-01: Delete the dead NJS JWT guard

**Priority:** P0
**Addresses:** F#16
**Effort:** 15 min
**Branch:** `security/T-01-remove-dead-njs-guard`

**Why:** `infra/nginx/njs/jwt-auth.js` explicitly documents (line 19) that it performs no signature verification. It is not currently wired into `nginx.conf`, but sits in the repo as a landmine for the next contributor who decides to "turn on JWT checking in nginx". Delete it.

**Files:**
- `infra/nginx/njs/jwt-auth.js` — delete
- `infra/nginx/Dockerfile` — remove `nginx-module-njs` line if nothing else needs it
- `compose/base.yml` — remove the `../infra/nginx/njs:/etc/nginx/njs:ro` volume mount (~line 227)

**Plan:**

1. `rm infra/nginx/njs/jwt-auth.js` and `rmdir infra/nginx/njs` if empty.
2. In `infra/nginx/Dockerfile`: remove the `RUN apk add --no-cache nginx-module-njs` line. Keep the base `FROM nginx:1.25-alpine`.
3. In `compose/base.yml`, delete the njs volume mount from the `nginx:` service.
4. If the platform ever needs NJS-level JWT validation, the replacement must fetch JWKS at nginx startup, verify RS256 signatures, and cache keys. Add a TODO in `docs/adr/` pointing at that future work rather than leaving broken code.

**Acceptance criteria:**

- `find infra/nginx/njs -type f 2>/dev/null` returns nothing.
- `grep -n "njs\|jwt-auth.js" infra/nginx/Dockerfile compose/base.yml` returns nothing.
- `docker compose -f compose/base.yml build nginx` succeeds.
- `docker compose -f compose/base.yml up -d nginx` succeeds; `curl -sI http://localhost/` returns a non-5xx.

**Commit:** `security(nginx): remove dead NJS JWT guard (F#16)`

---

### T-02: Lock down ai-controller pipeline registration and runtime

**Priority:** P0
**Addresses:** F#31, F#32, F#49
**Effort:** 2–3 h
**Branch:** `security/T-02-ai-controller-lockdown`

**Why:** `ai-controller` has the Docker socket mounted. Its pipeline-registration endpoints accept arbitrary `docker_image` values and have no role check. Any authenticated user (including patient tokens) can currently register a pipeline pointing at any Docker Hub image, trigger it, and escape to host-root. Additionally, spawned containers have no resource limits.

**Files:**
- `services/ai-controller/routers/pipelines.py`
- `services/ai-controller/routers/rules.py`
- `services/ai-controller/routers/jobs.py`
- `services/ai-controller/routers/saveback.py`
- `services/ai-controller/runner.py`
- `services/ai-controller/openhis.service.json`
- `.env.example`
- `tests/unit/ai-controller/test_pipelines_auth.py` — new

**Plan:**

1. **Add role checks.** In `pipelines.py`, add `Depends(require_roles("admin"))` to `POST`, `PATCH`, `DELETE`. Keep `GET` routes on `Depends(require_roles("admin", "radiologist"))`. Same tightening in `rules.py`. In `jobs.py` and `saveback.py`, ensure clinician/radiologist/admin coverage; patient role must not create or save back.
2. **Add an image allowlist.** In `runner.py`, read `POC_ALLOWED_IMAGES` env var (comma-separated). Before `client.containers.run`, reject if `img not in allowlist`. Default allowlist: `openhis/poc-xray:latest,openhis/poc-ct:latest`. Raise `RuntimeError("image not in allowlist")` which `run_job` already turns into a FAILED job.
3. **Validate pipeline IDs.** In `PipelineCreate` (Pydantic), add `id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")`. Same validator for `job_id` derivation — ensure `_prepare_input` rejects non-matching IDs.
4. **Add container resource limits.** In `_run_container_sync`, pass `mem_limit="2g"`, `memswap_limit="2g"` (disable swap), `pids_limit=256`, `nano_cpus=int(2 * 1e9)` (2 CPUs), `read_only=False` (pipelines write to `/data/jobs`), `security_opt=["no-new-privileges"]`, `cap_drop=["ALL"]`. Make these configurable via env vars (`POC_MEM_LIMIT`, `POC_CPU_LIMIT`, `POC_PIDS_LIMIT`) with sensible defaults.
5. **Bound log capture.** In `_run_container_sync`, call `container.logs(tail=1000)` instead of unbounded.
6. **Update `.env.example`** with `POC_ALLOWED_IMAGES`, `POC_MEM_LIMIT`, `POC_CPU_LIMIT`, `POC_PIDS_LIMIT`, each with a comment.
7. **Update `openhis.service.json`** `env.required` section.
8. **Tests.** Add `tests/unit/ai-controller/test_pipelines_auth.py` with 3 cases: (a) POST without token → 401, (b) POST with clinician role → 403, (c) POST with admin role → 201. See T-04 for how to run these without `DEV_MODE=true`.

**Acceptance criteria:**

- `grep -n "require_roles" services/ai-controller/routers/pipelines.py` shows deps on POST/PATCH/DELETE.
- `grep -n "POC_ALLOWED_IMAGES\|mem_limit\|pids_limit\|no-new-privileges" services/ai-controller/runner.py` shows all 3 patterns.
- `pytest tests/unit/ai-controller -v` passes, including the new auth tests.
- Manual check: `curl -X POST http://localhost/ai-controller/api/pipelines -d '{"id":"x","name":"x","docker_image":"alpine"}'` returns 401 without token, 403 with a clinician token.

**Commit:** `security(ai-controller): enforce admin role + image allowlist + resource limits (F#31, F#32, F#49)`

---

### T-03: Authenticate all admin routers (events, platform, audit)

**Priority:** P0
**Addresses:** F#15, F#66
**Effort:** 1 h
**Branch:** `security/T-03-admin-full-auth`

**Why:** `services/admin/routers/events.py` (SSE stream of every cross-service event) and `services/admin/routers/platform.py` (topology, profiles, ram) have no auth at all. `services/mpi/routers/audit.py` has no role check and no limit cap.

**Files:**
- `services/admin/routers/events.py`
- `services/admin/routers/platform.py`
- `services/mpi/routers/audit.py`
- `tests/unit/admin/test_events_auth.py` — new
- `tests/unit/mpi/test_audit_auth.py` — new

**Plan:**

1. In `admin/routers/events.py`: `from openhis_sdk.auth import require_roles`; add `dependencies=[Depends(require_roles("admin"))]` to both `/stream` and `/recent` routes.
2. In `admin/routers/platform.py`: same — add `dependencies=[Depends(require_roles("admin"))]` to `/topology`, `/profiles`, `/ram`.
3. In `mpi/routers/audit.py`: switch signature to `limit: int = Query(default=200, ge=1, le=1000)`; add `dependencies=[Depends(require_roles("admin"))]` on the router's only route. Import `Query` from fastapi if not already.
4. Add tests asserting 401 without token, 403 with wrong role, 200 with admin role. Use the T-04 test harness (deny-by-default).

**Acceptance criteria:**

- `grep -n "require_roles\|require_token" services/admin/routers/events.py services/admin/routers/platform.py services/mpi/routers/audit.py` shows a dep on every route.
- `curl -N http://localhost/admin/api/events/stream` without a token returns 401 (no more live SSE leaks).
- `curl 'http://localhost/mpi/api/audit?limit=99999999'` rejects with 422 (ValidationError on `le=1000`).

**Commit:** `security(admin,mpi): authenticate events, platform, audit endpoints (F#15, F#66)`

---

### T-04: Make DEV_MODE opt-in; add auth-enforcing test harness

**Priority:** P0
**Addresses:** F#2, F#33
**Effort:** 3–4 h
**Branch:** `test/T-04-auth-test-harness`

**Why:** `tests/conftest.py` globally sets `DEV_MODE=true`, bypassing every auth path. No test in the suite ever exercised a 401 or 403. Every other Phase 1 task needs this harness to actually prove the fixes work.

Additionally, the current `DEV_MODE` guard is "forbid `ENV=production`". It should be "require `ENV=development`", so that `ENV=staging` with `DEV_MODE=true` is a hard exit.

**Files:**
- `tests/conftest.py`
- `tests/unit/conftest.py` — new (if not present)
- `tests/auth/` — new directory
- `tests/auth/conftest.py` — new
- `tests/auth/test_every_service_rejects_no_token.py` — new
- `libs/openhis_sdk/src/openhis_sdk/auth.py`
- `.env.example`

**Plan:**

1. **Remove global DEV_MODE from `tests/conftest.py`.** Delete the `os.environ.setdefault("DEV_MODE", "true")` line. Keep the other stub env vars (`KEYCLOAK_URL=""`, etc.) only if they're needed for module import — but not `DEV_MODE`.

2. **Add `tests/unit/conftest.py`** that sets `DEV_MODE=true` for unit tests only (so existing tests don't break in bulk). This scopes the bypass to one directory instead of globally. Each unit test file that wants to exercise real auth can `monkeypatch.setenv("DEV_MODE", "false")` in a fixture.

3. **Add a new `tests/auth/` suite.** This is the detection harness. It spins up each FastAPI service's `app` with `DEV_MODE=false` and a mocked JWKS, and asserts:
   - Protected routes return 401 without a bearer token.
   - Protected routes return 403 with a token that's missing the required role.
   - Protected routes return 200 with a valid token.
   - Known-public routes (`/api/health`, `/docs`) return 200 without a token.
   Use `respx` or a fixture that mocks `validate_token` to return fabricated claims.

4. **Flip the DEV_MODE guard** in `libs/openhis_sdk/src/openhis_sdk/auth.py`:
   ```python
   if DEV_MODE:
       env = os.environ.get("ENV", "").lower()
       if env != "development":
           sys.exit(
               f"FATAL: DEV_MODE=true requires ENV=development (got ENV={env!r}). "
               "Never set DEV_MODE in staging or production."
           )
       log.warning("⚠️  DEV_MODE enabled — JWT validation is DISABLED.")
   ```
   Update the docstring at the top of `auth.py` to reflect the new rule.

5. **Update `.env.example`** comments around `DEV_MODE`/`ENV` to explain the new guard.

6. **Add a CI job** that runs `pytest tests/auth/` on every PR. Put it in `.github/workflows/ci.yml` as a separate job named `auth-tests`.

**Acceptance criteria:**

- `pytest tests/auth -v` runs and passes with at least one 401, one 403, and one 200 test per service that has protected routes.
- `pytest tests/unit tests/integration` still passes (DEV_MODE bypass scoped to `tests/unit/conftest.py`).
- Setting `DEV_MODE=true ENV=staging` in a running container causes an immediate `sys.exit`.
- A deliberate regression (temporarily remove `Depends(require_roles(...))` from an admin route) makes at least one `tests/auth/` test fail. Verify this before merging.

**Commit:** `test(auth): add real-auth test harness; scope DEV_MODE to unit tests; harden guard (F#2, F#33)`

---

### T-05: Add role checks to RIS orders/patients and MPI lookup

**Priority:** P0
**Addresses:** F#63, F#64, F#65
**Effort:** 1–2 h
**Branch:** `security/T-05-ris-mpi-role-checks`

**Why:** `services/ris/routers/orders.py` has role checks on POST/PATCH but none on `GET /orders/{id}`, `PUT /orders/{id}`, `DELETE /orders/{id}`. `services/ris/routers/patients.py` has no role checks at all. `services/mpi/routers/patients.py`'s `/lookup` uses `require_token` without a role check, allowing PHI enumeration by any authenticated user.

**Files:**
- `services/ris/routers/orders.py`
- `services/ris/routers/patients.py`
- `services/mpi/routers/patients.py`
- Tests: extend `tests/auth/test_every_service_rejects_no_token.py` (from T-04)

**Plan:**

1. In `ris/routers/orders.py`:
   - `GET /orders/{order_id}`: add `dependencies=[Depends(require_roles("clinician", "radiologist", "admin"))]`
   - `PUT /orders/{order_id}`: add `Depends(require_roles("radiologist", "admin"))`
   - `DELETE /orders/{order_id}`: add `Depends(require_roles("admin"))`
2. In `ris/routers/patients.py`:
   - `GET` (list) and `GET /{pid}`: `require_roles("clinician", "radiologist", "lab-tech", "admin")`
   - `POST`: `require_roles("clinician", "admin")`
   - `PATCH /{pid}`: `require_roles("clinician", "admin")`
   - `DELETE /{pid}`: `require_roles("admin")`
   - `POST /from-ehr`: `require_roles("clinician", "admin")`
3. In `mpi/routers/patients.py` `/lookup`:
   - Change from `Depends(require_token)` to `Depends(require_roles("clinician", "radiologist", "lab-tech", "admin"))`.
4. Extend the T-04 harness to cover each of these with 401/403/200 assertions.

**Acceptance criteria:**

- `grep -cE "require_roles\(" services/ris/routers/orders.py` returns ≥ 7 (all routes).
- `grep -cE "require_roles\(" services/ris/routers/patients.py` returns ≥ 6.
- `pytest tests/auth` passes with the new RIS/MPI cases.

**Commit:** `security(ris,mpi): add role checks to orders, patients, lookup (F#63, F#64, F#65)`

---

### T-06: Lock down integration-hub event ingest and simulator

**Priority:** P0
**Addresses:** F#20, F#38
**Effort:** 1–2 h
**Branch:** `security/T-06-hub-simulator-lockdown`

**Why:** `integration-hub` has `POST /events/report-final`, `/events/dicom-stored`, `/events/ai-job-completed`, and `/feed/trigger` behind JWTMiddleware but no role check — any user token can forge events. The `simulator` has no middleware and no auth at all, and nginx proxies it publicly.

**Files:**
- `services/integration-hub/app/routers/events.py`
- `services/integration-hub/app/routers/feed.py`
- `services/simulator/main.py`
- `services/simulator/Dockerfile` — add SDK (fixed in T-28)
- `compose/profiles/imaging.yml` — simulator build context (fixed in T-28)

**Plan:**

1. **integration-hub:** decide the access model. These endpoints are intended for service-to-service calls. Two options:
   - (a) Restrict to a specific role `service` issued via client_credentials flow — requires a Keycloak realm change.
   - (b) Restrict to roles the existing service accounts already have (`radiologist` for report-final, etc.).
   Pick (b) for now (no realm change needed). Add appropriate `require_roles(...)` to each POST endpoint. `GET /feed/status` can stay on `require_token`.
2. **simulator:** as a DICOM data generator it should only run in development. Add to `services/simulator/main.py`:
   ```python
   ENV = os.environ.get("ENV", "development").lower()
   if ENV != "development":
       sys.exit("FATAL: simulator is a dev-only tool. Do not enable in non-development ENV.")
   ```
   Also add `JWTMiddleware` with `require_roles("admin")` on the `POST /api/generate` endpoint. This needs the SDK available in the simulator's container — that's covered by T-28. Until T-28 lands, the ENV guard alone is the mitigation.
3. Extend T-04 harness with test cases for these endpoints.

**Acceptance criteria:**

- `grep -n "require_roles" services/integration-hub/app/routers/events.py services/integration-hub/app/routers/feed.py` shows a dep on every POST.
- `ENV=staging uvicorn main:app` in `services/simulator/` exits with the FATAL message.
- `pytest tests/auth` passes with the new cases.

**Commit:** `security(hub,simulator): role-gate event ingest; dev-only guard on simulator (F#20, F#38)`

---

### T-07: Fix BusConsumer ack semantics and add a dead-letter path

**Priority:** P0
**Addresses:** F#48
**Effort:** 3–4 h
**Branch:** `fix/T-07-bus-ack-on-success`

**Why:** `libs/openhis_sdk/src/openhis_sdk/bus.py` acks every message after calling the handler, regardless of whether the handler raised. Transient failures (DB locked, network blip) silently lose events. No reclaim of pending entries on consumer restart.

**Files:**
- `libs/openhis_sdk/src/openhis_sdk/bus.py`
- `tests/unit/sdk/test_bus.py` — new
- `docs/adr/0004-bus-dead-letter-semantics.md` — new

**Plan:**

1. In `BusConsumer`:
   - `_process` returns `True` on success, `False` on handler exception (don't swallow — re-raise up one level so `run` sees it).
   - `run` acks only on success. On failure, leave the entry in the pending list. Log with the entry ID and the event type so operators can find it.
   - Add a `max_delivery: int = 5` parameter. On the first read, also call `XAUTOCLAIM` to grab entries that have been pending longer than `idle_ms=30_000` from other consumers and re-process them, incrementing their delivery count. After `max_delivery` attempts, XADD the entry to `openhis:events:dlq` and XACK the original.
2. Add `openhis:events:dlq` as a convention. Update the README event-bus section.
3. Write an ADR (`docs/adr/0004-bus-dead-letter-semantics.md`) explaining: ack-on-success, pending reclaim via XAUTOCLAIM, DLQ after N attempts. Reference from `CLAUDE.md`.
4. Tests in `tests/unit/sdk/test_bus.py`:
   - Handler raises → entry stays in pending list, not acked.
   - Next loop → same entry is re-delivered to the same consumer.
   - After `max_delivery` attempts → entry lands on DLQ.
   - Handler succeeds → entry acked immediately.
   Use `fakeredis` for the in-memory Redis substitute (add to dev deps if not present).

**Acceptance criteria:**

- `pytest tests/unit/sdk/test_bus.py` passes with all four scenarios.
- ADR file exists and is linked from `CLAUDE.md`.
- Manually: kill the MPI consumer mid-handler; after restart, observe pending-list reclaim via `XPENDING openhis:events mpi`.

**Commit:** `fix(sdk/bus): ack-on-success, pending reclaim, DLQ after max_delivery (F#48)`

---

### T-08: Remove MLLP host port mapping

**Priority:** P0
**Addresses:** F#47
**Effort:** 30 min
**Branch:** `security/T-08-mllp-internal-only`

**Why:** `compose/base.yml` publishes `2575:2575` on the host. HL7 v2 over plain TCP carries PHI in cleartext (the parser extracts SSN). This is fine between containers on `openhis-net`, but not on the host's public interface.

**Files:**
- `compose/base.yml`
- `docs/explaining_the_project/security.md` (or a new `docs/operations/hl7-mllp.md`)
- `.env.example`
- `README.md`

**Plan:**

1. In `compose/base.yml`, remove the `ports: - "2575:2575"` block from the `hl7:` service. MLLP continues to be reachable from inside `openhis-net` at `hl7:2575`.
2. For external MLLP input, document two supported modes:
   - (a) Port-forward via `docker compose -f compose/overrides/mllp-public.yml up -d` — a new optional override that adds the port mapping, with a big red warning.
   - (b) MLLP over TLS (stunnel or similar). Document but don't implement.
3. Create `compose/overrides/mllp-public.yml` with the port mapping + a `command` logging a warning that it's public.
4. Update `.env.example` and `README.md` to explain: default is internal-only; operators must explicitly opt in.

**Acceptance criteria:**

- `grep -n "2575:2575" compose/base.yml` returns nothing.
- `docker compose -f compose/base.yml -f compose/overrides/mllp-public.yml up -d hl7 && nc -zv localhost 2575` succeeds.
- `docker compose -f compose/base.yml up -d hl7 && nc -zv localhost 2575` fails (connection refused).

**Commit:** `security(hl7): remove public MLLP port; require explicit override for host exposure (F#47)`

---

### T-09: Template Keycloak realm secrets at opm init

**Priority:** P0
**Addresses:** F#17, F#68, F#78
**Effort:** 1 day
**Branch:** `security/T-09-keycloak-realm-templating`

**Why:** `infra/keycloak/openhis-realm.json` ships with 9 plaintext client secrets and a realm HMAC key. `opm init` does not touch it. Three infra configs (`openmrs/oauth2.properties`, `odoo/odoo.conf`, `openelis/common.properties`) also ship with matching hardcoded values. Any deployer who fills `.env` gets a stack running on the committed secrets.

**Files:**
- `infra/keycloak/openhis-realm.json.j2` — new (rename of current realm JSON, converted to Jinja)
- `infra/keycloak/openhis-realm.json` — becomes a `.gitignore`'d rendered artifact
- `infra/openmrs/oauth2.properties.j2` — new
- `infra/openmrs/oauth2.properties` — becomes `.gitignore`'d
- `infra/odoo/odoo.conf.j2` — new (for OIDC block only; DB password handled in T-34)
- `infra/odoo/odoo.conf` — rendered
- `infra/openelis/common.properties.j2` — new (OIDC client secret portion)
- `infra/openelis/common.properties` — rendered
- `platform/opm.py` — add `render_infra` command + integrate into `init`
- `.gitignore`
- `.env.example`

**Plan:**

1. **Convert realm JSON to a Jinja template.**
   - Copy `infra/keycloak/openhis-realm.json` to `infra/keycloak/openhis-realm.json.j2`.
   - Replace each literal `"secret" : "<value>"` with `"secret" : "{{ <VAR_NAME> }}"`. Mapping:
     - `openhis-platform-secret` → `KEYCLOAK_CLIENT_SECRET`
     - `integration-hub-sa-secret` → `INTEGRATION_HUB_KC_CLIENT_SECRET`
     - `hl7-sa-secret` → `HL7_KC_CLIENT_SECRET`
     - `analytics-sa-secret` → `ANALYTICS_KC_CLIENT_SECRET`
     - `ris-sa-secret` → `RIS_KC_CLIENT_SECRET`
     - `patient-portal-sa-secret` → `PATIENT_PORTAL_KC_CLIENT_SECRET`
     - `odoo-oidc-secret` → `ODOO_OIDC_SECRET`
     - `openelis-oidc-secret` → `OPENELIS_OIDC_SECRET`
     - `openmrs-keycloak-secret` → `OPENMRS_KC_CLIENT_SECRET`
   - The realm HMAC key block (lines ~2184–2196 with `kid`, `secret`, `certificate`) should NOT be templated — delete those two key entries entirely and let Keycloak regenerate them on first `--import-realm` with fresh material.
2. **Convert the three infra config files** to `.j2` counterparts, templating only the shared secret values. Keep DB passwords for T-34.
3. **Add `opm render-infra` command** in `platform/opm.py`:
   - Reads `.env`.
   - For each `.j2` file under `infra/`, renders to the same path without `.j2`, using Jinja with `undefined=StrictUndefined` (fail fast on missing vars).
   - Called automatically at the end of `opm init`, and as a standalone command (`opm render-infra --validate`).
4. **Add the rendered files to `.gitignore`**:
   ```
   infra/keycloak/openhis-realm.json
   infra/openmrs/oauth2.properties
   infra/odoo/odoo.conf
   infra/openelis/common.properties
   ```
   Commit the `.j2` templates; the actual rendered files become per-deployment artifacts.
5. **Create initial rendered files for the demo** (so cloning + `make up` still works): add an `opm demo-render` command that renders the templates with deliberately weak, clearly-dev values (e.g. `dev-openhis-platform-secret-do-not-use`). README's Quick Start points at `opm init` for anything else.
6. **Update `.env.example`** to add all new template variables with `CHANGE_ME_BEFORE_DEPLOY` defaults.
7. **Update CHANGELOG.md** — this is a breaking change; existing `.env` files are incomplete and need migration.

**Acceptance criteria:**

- `git ls-files infra/keycloak/openhis-realm.json infra/openmrs/oauth2.properties infra/odoo/odoo.conf infra/openelis/common.properties` returns nothing.
- `grep -rE 'openhis-platform-secret|integration-hub-sa-secret|hl7-sa-secret|analytics-sa-secret|ris-sa-secret|patient-portal-sa-secret|odoo-oidc-secret|openelis-oidc-secret|openmrs-keycloak-secret' infra/ platform/ libs/ services/ | grep -v '\.j2:' | grep -v 'CHANGELOG\|REMEDIATION_PLAN\|\.env\.example:'` returns nothing.
- `opm render-infra` writes all four files, each containing the `.env` values.
- `opm render-infra` with an unset variable fails with a clear error.
- Fresh Keycloak start on a rendered realm accepts service-account logins using the `.env` values.

**Commit:** `security(keycloak): template realm JSON and infra configs from .env (F#17, F#68, F#78)`

---

### T-10: Fix opm init to cover all secrets

**Priority:** P0
**Addresses:** F#45, F#46
**Effort:** 3 h
**Branch:** `security/T-10-opm-init-complete-secrets`

**Why:** Current `opm init` prompts for 4 keys out of the 20 declared in `.env.example`. Every missing secret falls back to `admin`/`admin` defaults via docker-compose's `${VAR:-default}` pattern. The `--validate` flag checks the just-populated dict so can never trigger.

**Files:**
- `platform/opm.py`
- `.env.example`
- `tests/unit/platform/test_opm_init.py` — new

**Plan:**

1. **Expand the credential list.** Define in `opm.py`:
   ```python
   _REQUIRED_SECRETS = [
       "POSTGRES_PASSWORD", "MPI_DB_PASS",
       "ADMIN_PASS",
       "KEYCLOAK_ADMIN_PASSWORD", "KEYCLOAK_CLIENT_SECRET",
       "INTEGRATION_HUB_KC_CLIENT_SECRET", "HL7_KC_CLIENT_SECRET",
       "ANALYTICS_KC_CLIENT_SECRET", "RIS_KC_CLIENT_SECRET",
       "PATIENT_PORTAL_KC_CLIENT_SECRET", "OPENMRS_KC_CLIENT_SECRET",
       "ODOO_MASTER_PASS", "ODOO_ADMIN_PASS", "ODOO_OIDC_SECRET",
       "OPENELIS_OIDC_SECRET",
   ]
   ```
2. **Default to auto-generate.** Add `--auto-generate/--prompt` flag (default auto-generate). Auto-generate uses `secrets.token_urlsafe(32)` for each missing secret. Print a summary of how many were generated vs prompted.
3. **`--non-interactive` mode** must either have all secrets set via env vars / flags, or use `--auto-generate`. Remove the implicit default-to-`admin` behavior.
4. **Fix `--validate`.** After writing `.env`, re-read it from disk and fail if any value is empty, matches `CHANGE_ME_BEFORE_DEPLOY`, or has entropy below some threshold (use `zxcvbn-python` or a simple character-class check: ≥ 16 chars, ≥ 3 character classes). Apply `--validate` always; make `--no-validate` the explicit opt-out.
5. **Preserve comments in `.env`.** Current `_write_env` drops them. Either read `.env.example` first and write over it while preserving comment lines, or switch to a proper dotenv library (`python-dotenv` can round-trip comments with `dotenv_values` + a custom writer).
6. **Call `opm render-infra` at the end of `init`** (added in T-09).
7. **Tests** in `tests/unit/platform/test_opm_init.py`:
   - `init --non-interactive --auto-generate` populates all 15 secrets with ≥ 32-char values.
   - `init --non-interactive` without `--auto-generate` and without all flags → exit 1 with missing-vars message.
   - `init --validate` refuses to write if any env value is `CHANGE_ME_BEFORE_DEPLOY`.
   - Comments in `.env.example` are preserved in the written `.env`.

**Acceptance criteria:**

- After a fresh `opm init --non-interactive --auto-generate`, `grep -cE '^[A-Z_]+=[A-Za-z0-9_-]{32,}' .env` ≥ 15.
- `grep -c 'CHANGE_ME_BEFORE_DEPLOY' .env` returns 0.
- `pytest tests/unit/platform/test_opm_init.py` passes.

**Commit:** `security(opm): cover all credentials in init; auto-generate by default; validate .env on disk (F#45, F#46)`

---

## Phase 2 — Detection infrastructure (P1)

Without these, Phase 3+ regressions can't be caught in CI. Do all of Phase 2 before Phase 3.

---

### T-11: Migrate admin service to the SDK, fix CI drift detection

**Priority:** P1
**Addresses:** F#1, F#7, F#11, F#21
**Effort:** 2 h
**Branch:** `refactor/T-11-admin-sdk-migration`

**Why:** Admin is the only service with a real-logic `jwt_auth.py` (106 lines) instead of the 3-line shim. `services/admin/main.py:59` does a lazy `from jwt_auth import KEYCLOAK_URL, KEYCLOAK_REALM` that will break the moment the file is shimmed. The CI `sdk-lint` regex (`class JWTMiddleware`, `def configure`) doesn't match admin's duplicate because admin's file has neither string.

**Files:**
- `services/admin/jwt_auth.py` — replace with shim
- `services/admin/main.py` — update lazy import, add JWTMiddleware
- `services/admin/routers/*.py` — confirm all `Depends(require_token)` still work (they already import from `jwt_auth`, which re-exports from SDK)
- `.github/workflows/ci.yml` — fix drift regex

**Plan:**

1. Replace `services/admin/jwt_auth.py` with:
   ```python
   # Re-exports from the shared SDK — do not add logic here.
   from openhis_sdk.auth import (  # noqa: F401
       JWTMiddleware, require_token, require_roles,
       KEYCLOAK_URL, KEYCLOAK_REALM, KEYCLOAK_AUDIENCE,
   )
   ```
2. In `admin/main.py`:
   - Change line 59 to `from openhis_sdk.auth import KEYCLOAK_URL, KEYCLOAK_REALM` (or just use the shim).
   - Add `from openhis_sdk.auth import JWTMiddleware; app.add_middleware(JWTMiddleware)` after router includes. Remove per-endpoint `Depends(require_token)` from the 7 routers — the middleware replaces them. Keep `require_roles(...)` role checks where present.
3. Fix the CI drift regex. Replace both greps in `.github/workflows/ci.yml` with:
   ```bash
   found=$(find services/ -name 'jwt_auth.py' -exec wc -l {} \; | awk '$1 > 10 {print $2}')
   ```
   Any `jwt_auth.py` with more than 10 lines outside `libs/` is a violation. Same for `log_config.py`. Update the step names.
4. Same for any future `_JWKS_CACHE` / `_validate_jwt` stray copies — add a second lint that greps for those symbol names outside `libs/openhis_sdk/`.

**Acceptance criteria:**

- `wc -l services/admin/jwt_auth.py` returns ≤ 10.
- `pytest tests/unit/admin` passes.
- `pytest tests/auth` — the admin test cases — still pass (admin routes still require auth).
- Introducing a 20-line local `jwt_auth.py` in any service makes the CI `sdk-lint` job fail.

**Commit:** `refactor(admin): migrate to openhis_sdk; fix CI drift detection (F#1, F#7, F#11, F#21)`

---

### T-12: Add pre-commit / CI lint for banned patterns

**Priority:** P1
**Addresses:** F#67 (also prevents future recurrence of F#1)
**Effort:** 2 h
**Branch:** `chore/T-12-ruff-bans`

**Why:** 20 `datetime.utcnow()` violations slipped in despite the hard rule in `CLAUDE.md`. Same pattern will keep happening without mechanical enforcement.

**Files:**
- `ruff.toml` or `pyproject.toml` at repo root — new/updated
- `.pre-commit-config.yaml` — new
- `.github/workflows/ci.yml` — add a lint job
- All files listed below under "cleanup sweep"

**Plan:**

1. **Add `ruff` config.** At repo root, create `pyproject.toml` (or extend existing) with:
   ```toml
   [tool.ruff]
   target-version = "py311"
   line-length = 110
   exclude = ["services/_legacy", "**/_legacy/**"]

   [tool.ruff.lint]
   select = ["E", "F", "W", "I", "UP", "B", "DTZ", "S"]
   # DTZ003: datetime.utcnow deprecated
   # DTZ005: datetime.now without tz
   # S105/S106: possible hardcoded secrets
   ignore = ["E501"]  # line length handled by formatter

   [tool.ruff.lint.per-file-ignores]
   "tests/**/*.py" = ["S"]  # test helpers need some of the S rules relaxed
   ```
2. **Add pre-commit config:**
   ```yaml
   repos:
     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.6.9
       hooks:
         - id: ruff
           args: [--fix]
         - id: ruff-format
   ```
3. **Add CI job.** In `.github/workflows/ci.yml`, add:
   ```yaml
   lint:
     name: Lint
     runs-on: ubuntu-latest
     steps:
       - uses: actions/checkout@v4
       - uses: astral-sh/ruff-action@v1
         with:
           version: 0.6.9
   ```
4. **Cleanup sweep.** Run `ruff check --fix .` and then `ruff check . --select DTZ --no-fix` to list remaining DTZ violations. Manually fix each — replace `datetime.utcnow()` with `datetime.now(timezone.utc)`. Affected files (from audit):
   - `services/mpi/routers/matching.py`
   - `services/mpi/routers/patients.py`
   - `services/hl7/routers/messages.py`
   - `services/integration-hub/app/translators/diagnostic_report.py`
   - `services/integration-hub/app/translators/imaging_study.py`
   - `services/integration-hub/app/translators/observation.py`
   - `services/admin/routers/registry.py`
   - `services/admin/routers/config.py`
   - `services/admin/routers/services.py`
   - `services/patient-portal/auth.py`
   - `services/analytics/collector.py`
5. **Document.** Add a `## Linting` section to `CONTRIBUTING.md`. Mention `pre-commit install`.

**Acceptance criteria:**

- `ruff check .` returns 0 violations.
- `grep -rn "datetime\.utcnow\|datetime\.datetime\.utcnow" services/ libs/ platform/ pipelines/ | grep -v _legacy` returns nothing.
- A deliberate `datetime.utcnow()` insertion in any non-legacy file makes CI fail.

**Commit:** `chore: add ruff lint; ban datetime.utcnow (F#67)`

---

### T-13: Fix CI pytest paths, Makefile test-service, and CLAUDE.md references

**Priority:** P1
**Addresses:** F#5, F#22, F#62
**Effort:** 1 h
**Branch:** `chore/T-13-fix-test-paths`

**Why:** `.github/workflows/ci.yml` runs `pytest tests/mpi/`, `tests/hl7/`, etc. — paths that don't exist (actual layout is `tests/unit/<service>/`). `Makefile`'s `test-service` target has the same bug. `CLAUDE.md` has multiple broken `@docs/...` references pointing at directories with different naming (hyphens vs underscores, `openhissdk` vs `openhis_sdk`, etc.).

**Files:**
- `.github/workflows/ci.yml`
- `Makefile`
- `CLAUDE.md`

**Plan:**

1. **CI.** In `ci.yml`, change every `pytest tests/<service>/` to `pytest tests/unit/<service>/`. There are 9 such lines.
2. **Makefile.** Change the `test-service` target from `pytest tests/$(SVC)/` to `pytest tests/unit/$(SVC)/`. Document: "SVC is the service name, e.g., `mpi`, `ris`".
3. **CLAUDE.md.** Do a find-and-replace:
   - `@docs/explaining-the-project/` → `@docs/explaining_the_project/`
   - `@docs/guidelines-for-contributors/` → `@docs/guidelines_for_contributors/`
   - `libs/openhissdk` → `libs/openhis_sdk`
   - `services-legacy` / `services/legacy` → `services/_legacy`
   - `openhissdk` (bare) → `openhis_sdk`
   - `pytest tests/mpi` → `pytest tests/unit/mpi` (and similar)
   - `nginxgen.py` → `nginx_gen.py`
4. Verify every `@`-reference in `CLAUDE.md` resolves to a real file on disk.

**Acceptance criteria:**

- `for f in $(grep -oE '@docs/[a-zA-Z0-9/_\.-]+' CLAUDE.md | sed 's/@//'); do test -e "$f" || echo "MISSING: $f"; done` returns nothing.
- `make test-service SVC=mpi` runs `pytest tests/unit/mpi/` and at least one test executes.
- `grep -n "tests/<svc>\|pytest tests/mpi\|pytest tests/hl7" .github/workflows/ci.yml Makefile CLAUDE.md` returns nothing.

**Commit:** `chore: fix pytest paths in CI, Makefile, CLAUDE.md (F#5, F#22, F#62)`

---

## Phase 3 — Correctness and contract reconciliation (P1)

---

### T-14: Fix adapter error handling to match contract

**Priority:** P1
**Addresses:** F#34
**Effort:** 3 h
**Branch:** `fix/T-14-adapter-error-handling`

**Why:** `docs/explaining_the_project/adapter-contract.md §6` explicitly requires adapters to raise on failure. `services/integration-hub/app/services/openmrs.py` and the others swallow all exceptions and return `[]` / `None` / `False`. This breaks the retry queue `worker.py` was built around.

**Files:**
- `services/integration-hub/app/services/openmrs.py`
- `services/integration-hub/app/services/openelis.py`
- `services/integration-hub/app/services/odoo.py`
- `services/integration-hub/app/worker.py` (verify exception handling)
- `tests/unit/integration-hub/test_adapters_raise.py` — new

**Plan:**

1. In each adapter, remove the outer `try/except Exception` that catches and returns a sentinel. Let `httpx.HTTPStatusError` propagate from `r.raise_for_status()`. Keep the narrow `except Exception` only around operations where it's genuinely safe (e.g. JSON decode of a *probably* empty response body).
2. For read operations (`get_recent_patients`, `get_active_service_requests`, etc.): distinguish between "no results" (empty list is fine) and "error" (raise). HTTP 200 with empty `entry` array → return `[]`. HTTP 5xx → raise.
3. In `worker.py`, verify it already catches adapter exceptions and places items on the retry queue. If it just lets the worker crash, wrap calls in `try/except` that logs + audit-entries + retry-queue-push.
4. Tests:
   - Adapter receives 500 → raises `httpx.HTTPStatusError`.
   - Adapter receives 200 with empty body → returns sensible default.
   - Worker with a failing adapter enqueues to retry, does not crash.

**Acceptance criteria:**

- `grep -cE "except Exception" services/integration-hub/app/services/*.py` is substantially lower (from 11 to ≤ 3 genuine narrow cases).
- `pytest tests/unit/integration-hub` passes.
- Integration test: inject a 500 into OpenMRS, watch worker.py place the item on the retry queue (via audit log or `state`).

**Commit:** `fix(adapters): raise on failure per adapter contract §6 (F#34)`

---

### T-15: Reconcile adapter contract with reality

**Priority:** P1
**Addresses:** F#35, F#36, F#37
**Effort:** 1 h
**Branch:** `docs/T-15-adapter-contract-reconcile`

**Why:** Contract §3 says every adapter must expose `get_recent_patients` and `upsert_patient`; only one adapter has each. Contract §7 says "no mocks at adapter boundary", CLAUDE.md says "integration tests use respx". CLAUDE.md's `worker.py` dedup warning is stale.

**Files:**
- `docs/explaining_the_project/adapter-contract.md`
- `CLAUDE.md`

**Plan:**

1. **Rewrite adapter-contract §3** with a role-based shape:
   - Every adapter must implement: `async def health_check() -> bool`.
   - **Source adapters** (read from external into OpenHIS): `async def get_recent_<resource>(...) -> list[dict]` for each resource they source.
   - **Sink adapters** (write from OpenHIS into external): `async def upsert_<resource>(...) -> str | None` for each resource they sink.
   - **Bidirectional adapters** implement both sets.
   - Current adapters map:
     - `openmrs`: source for patients + service requests; sink for diagnostic reports.
     - `openelis`: sink for patients + service requests; source for diagnostic reports.
     - `odoo`: sink for patients; sink for pharmacy orders.
   Document this table explicitly.
2. **Reconcile §7 with CLAUDE.md.** Update §7 to: *"Integration tests use `respx` to mock HTTP at the adapter boundary. A separate `tests/smoke/` suite exercises real container instances of each external system."* Remove the "no mocks at adapter boundary" line — it's not what the team does.
3. **Update CLAUDE.md** section "Gotchas & Warnings": remove the stale dedup warning (now Redis-backed with 7-day TTL in `worker.py`); add a note about the new contract shape.

**Acceptance criteria:**

- `docs/explaining_the_project/adapter-contract.md` is internally consistent with `CLAUDE.md` and with what the adapters actually do.
- `grep -n "synced_patients\|synced_orders" CLAUDE.md` returns nothing (stale warning gone).

**Commit:** `docs(adapter): reconcile contract with reality; refresh CLAUDE.md (F#35, F#36, F#37)`

---

### T-16: Fix MPI matcher — diacritics and threshold review

**Priority:** P1
**Addresses:** F#50, F#51
**Effort:** 2 h
**Branch:** `fix/T-16-mpi-matcher-diacritics`

**Why:** `_norm` at `services/mpi/matcher.py:20-23` strips diacritics via ASCII filter ("José" → "jos"). Systematic under-matching on non-English names. Threshold (0.70) accepts lastname + DOB + sex alone as a duplicate — too generous.

**Files:**
- `services/mpi/matcher.py`
- `tests/unit/mpi/test_matcher.py` — new or extended
- `docs/adr/0005-mpi-matcher-threshold.md` — new

**Plan:**

1. **Fix `_norm`:**
   ```python
   import unicodedata
   def _norm(s) -> str:
       if not s:
           return ""
       # Strip accents first, then keep only alphanumerics
       nfkd = unicodedata.normalize('NFKD', str(s))
       stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
       return re.sub(r"[^a-z0-9]", "", stripped.lower())
   ```
2. **Add phonetic matching as a secondary signal.** Modify `compute_match_score` to add `0.05 × (jellyfish.metaphone(a) == jellyfish.metaphone(b))` per name. Keep the main Jaro-Winkler weights; phonetic is a bonus that helps with typos.
3. **Threshold.** Keep `0.70` as the default but make it a parameter of `find_candidates` (already is). Write an ADR explaining the choice:
   - At 0.70, lastname + DOB + sex matches as duplicate — aggressive.
   - At 0.82, requires firstname ≥ ~0.95 Jaro-Winkler plus DOB + lastname perfect.
   - Recommend 0.75 as a safer default for new deployments; keep 0.70 for back-compat with existing MPIs.
4. **Tests:**
   - `compute_match_score({"firstname":"José"},{"firstname":"Jose"})` ≥ 0.9 after normalization.
   - Aliases: `Müller`/`Muller`, `Sørensen`/`Sorensen`, `García`/`Garcia` all ≥ 0.9.
   - Add a threshold sensitivity test: known-false-positive pair (same lastname + DOB + sex, different firstnames) scores between 0.70 and 0.82, demonstrating the threshold choice matters.

**Acceptance criteria:**

- `pytest tests/unit/mpi/test_matcher.py -v` passes with the new diacritic cases.
- `docs/adr/0005-mpi-matcher-threshold.md` exists and is referenced from `CLAUDE.md`.

**Commit:** `fix(mpi): handle diacritics in matcher; add phonetic bonus; ADR on threshold (F#50, F#51)`

---

### T-17: Fix HL7 — repeated segments, Redis pooling, module shadowing, maxlen

**Priority:** P1
**Addresses:** F#52, F#53, F#54, F#55
**Effort:** 3 h
**Branch:** `fix/T-17-hl7-quality`

**Why:** `services/hl7/parser.py:47-48` drops all repeated segments (OBX, IN1, NK1, etc.) — broken for ORU^R01 with multiple analytes. `handlers.py` re-creates a Redis client per event publish. `from token import get_service_token` shadows Python's stdlib `token` module. `handlers.py` uses `maxlen=10_000`, SDK uses `maxlen=50_000` — same stream, oscillating cap.

**Files:**
- `services/hl7/parser.py`
- `services/hl7/handlers.py`
- `services/hl7/token.py` — rename to `service_token.py`
- `services/hl7/main.py` — wire a shared Redis client
- `libs/openhis_sdk/src/openhis_sdk/bus.py` (for centralized maxlen)
- `tests/unit/hl7/test_parser_repeated_segments.py` — new

**Plan:**

1. **Parser: keep repeated segments.**
   ```python
   segments: dict[str, list[list]] = {}   # note: list of segments per name
   for line in lines:
       name = line[:3].upper()
       fields = ...
       segments.setdefault(name, []).append(fields)
   ```
   - Keep existing single-access helpers by accessing `segments.get("MSH", [[]])[0]` etc.
   - Add a new `segments_all(name)` accessor for callers that need all OBX.
   - Add `observations` to the returned dict: list of dicts, one per OBX, extracting value type + value + units.
2. **Redis pooling.** In `handlers.py`:
   - Remove the per-call `aioredis.from_url()` + `aclose()`.
   - Add a module-level `_REDIS` and a `get_redis()` accessor that initializes once.
   - Register a shutdown hook in `main.py`'s lifespan that calls `await _REDIS.aclose()` on shutdown.
   - Prefer: import and use `openhis_sdk.bus.publish_event(client, type, payload)` with the client injected. This removes the duplicate code entirely.
3. **Module rename.** `git mv services/hl7/token.py services/hl7/service_token.py`. Update all imports:
   ```bash
   grep -rln "from token import" services/hl7/ | xargs sed -i 's/from token import/from service_token import/'
   ```
4. **Centralize stream maxlen.** In `libs/openhis_sdk/src/openhis_sdk/bus.py`, export `STREAM_MAXLEN = 50_000` as a module constant. Have `publish_event` use it. Replace the hardcoded `10_000` in `handlers.py` with `openhis_sdk.bus.STREAM_MAXLEN` — or better, use `publish_event` directly.
5. **Tests:** parse an ORU^R01 with 3 OBX segments; assert all 3 observations are in the result dict.

**Acceptance criteria:**

- `pytest tests/unit/hl7 -v` passes with the new parser tests.
- `grep -rn "aioredis.from_url" services/hl7/` returns ≤ 1 (only in the factory).
- `find services/hl7/ -name token.py` returns nothing.
- `grep -rn "maxlen=10_000\|maxlen=10000" services/` returns nothing.

**Commit:** `fix(hl7): keep repeated segments; pool redis; rename token.py; centralize stream maxlen (F#52-55)`

---

### T-18: Fix FHIR translator edge cases

**Priority:** P1
**Addresses:** F#69, F#70, F#71
**Effort:** 1 h
**Branch:** `fix/T-18-fhir-translator-edge-cases`

**Why:** Translators use `datetime.utcnow()` (covered by T-12 but flagged here for context). `valueQuantity.value = None` when parse fails — produces invalid FHIR. `subject.reference = "Patient/None"` when `ehr_patient_id` is missing.

**Files:**
- `services/integration-hub/app/translators/diagnostic_report.py`
- `services/integration-hub/app/translators/observation.py`
- `services/integration-hub/app/translators/imaging_study.py`
- `tests/unit/integration-hub/test_translators.py` — new or extended

**Plan:**

1. In `_try_float`: if parsing fails, return `None` still, but in the caller check — if `value` is None, emit `valueString` (with the raw text) instead of `valueQuantity`. Never emit `valueQuantity.value = None`.
2. `subject.reference`: if `ehr_patient_id` is missing or falsy, raise `ValueError("cannot translate without ehr_patient_id")`. Do not emit `"Patient/None"`. Worker then sends the event to the retry queue.
3. Sanitize numeric values: reject `float('inf')`, `float('nan')` — FHIR values must be finite.
4. Tests for each case.

**Acceptance criteria:**

- `pytest tests/unit/integration-hub/test_translators.py` passes with new edge cases.
- Translators no longer produce `"Patient/None"` references.

**Commit:** `fix(translators): handle missing patient id and non-numeric values per FHIR (F#69-71)`

---

### T-19: Fix remaining opm commands — config auth, upgrade wait, subprocess timeouts

**Priority:** P1
**Addresses:** F#58, F#73, F#74
**Effort:** 2 h
**Branch:** `fix/T-19-opm-commands`

**Why:** `opm config set/get/list` and `opm status` call admin API with no token; always hit the 401 fallback silently. `opm upgrade` promises a health wait but doesn't wait. Every `subprocess.run` is timeout-less.

**Files:**
- `platform/opm.py`
- `platform/nginx_gen.py`

**Plan:**

1. **Acquire a token for admin calls.** Add a `_get_service_token()` helper that does client_credentials against Keycloak using the `opm`'s own SA client (create one if needed; can reuse `admin-cli` initially). Cache the token for its TTL. Attach `Authorization: Bearer <token>` to every `requests.get/put` in `opm config` and `opm status`.
2. **Upgrade health wait.** After `up -d --no-deps svc`, poll `docker inspect --format '{{.State.Health.Status}}' <container>` every 5s for up to 300s (configurable via `--timeout` flag). If it doesn't reach `healthy`, prompt to continue (current behavior) or abort.
3. **Subprocess timeouts.** Every `subprocess.run` in `opm.py` and `nginx_gen.py` gets `timeout=60` (configurable per-call). On `subprocess.TimeoutExpired`, print a clear message and exit 2.
4. Minor: `reload_nginx` in `nginx_gen.py` — add `timeout=10`.

**Acceptance criteria:**

- `opm config list` with a running admin service returns the config list (previously fell back to error).
- `opm status` shows the service table from the admin registry (previously always fell back to docker ps).
- `opm upgrade` waits for each service to become healthy before moving on.
- `grep -n "subprocess.run" platform/*.py | grep -v "timeout="` returns nothing.

**Commit:** `fix(opm): authenticate admin API calls; wait for health on upgrade; subprocess timeouts (F#58, F#73, F#74)`

---

### T-20: Fix opm add-service scaffold to produce compliant services

**Priority:** P1
**Addresses:** F#72
**Effort:** 2 h
**Branch:** `fix/T-20-opm-add-service-scaffold`

**Why:** Current scaffold produces `python:3.12-slim` (inconsistent), no SDK, no JWTMiddleware, no `_check_env`, no log_config. Violates every CLAUDE.md rule.

**Files:**
- `platform/opm.py` `add_service` function
- `platform/templates/service/` — new directory for templates

**Plan:**

1. **Move the service scaffold into template files** under `platform/templates/service/`:
   - `main.py.j2` — with `JWTMiddleware`, `_check_env`, `lifespan` context, SDK imports, log_config call.
   - `Dockerfile.j2` — `FROM python:3.11-slim`, copies `libs/openhis_sdk`, installs SDK.
   - `requirements.txt.j2` — pinned versions of `fastapi`, `uvicorn`, `httpx`, `python-jose`.
   - `openhis.service.json.j2` — with `env.required` block pre-populated with Keycloak vars.
   - `routers/__init__.py.j2` and `routers/health.py.j2`.
2. **Render them in `add_service`** using Jinja.
3. **Add a compose snippet** the user can paste — include `context: ${PWD}`, `dockerfile: services/<n>/Dockerfile`, env vars for Keycloak SA, network attachment.
4. **Verify the scaffolded service** passes the T-04 auth harness and the T-12 lint. Add a CI smoke test: `opm add-service foo --profile analytics --port 8099 && pytest tests/auth -k foo`.

**Acceptance criteria:**

- `opm add-service foo` creates a service whose `main.py` contains `JWTMiddleware`, `configure`, `_check_env`.
- `ruff check services/foo/` passes.
- `docker build -f services/foo/Dockerfile .` succeeds.

**Commit:** `feat(opm): scaffold compliant services with SDK, middleware, lifecycle (F#72)`

---

### T-21: Implement data retention policies

**Priority:** P1
**Addresses:** F#76
**Effort:** 3 h
**Branch:** `feat/T-21-retention`

**Why:** `.env.example:96-97` declares `PATIENT_DATA_RETENTION_YEARS` and `AUDIT_RETENTION_DAYS` with HIPAA/GDPR comments. Neither is consulted by any code. It's regulatory theater.

**Files:**
- `services/integration-hub/app/db/audit.py`
- `services/admin/database.py` and related audit table migrations
- `services/mpi/database.py` and related
- A new `openhis_sdk.retention` helper — optional
- CI job or cron script — documented, not necessarily implemented

**Plan:**

1. **Add a retention helper in `openhis_sdk`:**
   ```python
   # libs/openhis_sdk/src/openhis_sdk/retention.py
   def audit_cutoff(days: int) -> str:
       return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
   ```
2. **Integration-hub audit.** Add `async def prune_audit(keep_days: int) -> int` that deletes rows where `ts < cutoff`. Wire it into the `worker.py` main loop: once per hour, call with `int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))`.
3. **Admin audit + MPI audit.** Same pattern — a `prune_audit` per service and a periodic task in each lifespan.
4. **Patient data retention is harder** because the data is in OpenMRS/OpenELIS. Document: `PATIENT_DATA_RETENTION_YEARS` is not enforced today; add a `TODO` to the `retention` helper and a note in `CLAUDE.md`/README that retention is currently audit-only.
5. Add config knob to turn off the sweeper (`AUDIT_RETENTION_DAYS=0` disables).

**Acceptance criteria:**

- Set `AUDIT_RETENTION_DAYS=1`, insert audit rows with `ts` from 3 days ago, wait one sweeper cycle, rows are gone.
- `AUDIT_RETENTION_DAYS=0` disables sweeper entirely (nothing deleted).
- Unit tests for `prune_audit` with synthetic rows.

**Commit:** `feat(retention): actually enforce AUDIT_RETENTION_DAYS across audit logs (F#76)`

---

## Phase 4 — Hardening and supply chain (P2)

---

### T-22: nginx security headers, rate limits, location hardening

**Priority:** P2
**Addresses:** F#24, F#25, F#59
**Effort:** 3 h
**Branch:** `security/T-22-nginx-hardening`

**Files:**
- `infra/nginx/nginx.conf.j2`
- `platform/nginx_gen.py` — allowlist regex for profile-supplied paths

**Plan:**

1. **Add security headers** in the `server` block:
   ```
   add_header X-Frame-Options "SAMEORIGIN" always;
   add_header X-Content-Type-Options "nosniff" always;
   add_header Referrer-Policy "strict-origin-when-cross-origin" always;
   add_header Content-Security-Policy "default-src 'self'; ..." always;
   ```
   CSP will take tuning per profile — start permissive, tighten in a follow-up.
2. **Add rate limits** in `http`:
   ```
   limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;
   limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/s;
   ```
   Apply `limit_req zone=api burst=60 nodelay;` to `/admin/`, `/mpi/`, `/integration-hub/`, etc. Apply `limit_req zone=auth burst=10 nodelay;` to `/keycloak/`.
3. **Fix broad `location /web` match.** Change to `location ^~ /web/ {` so it can't be hijacked.
4. **Profile path validation.** In `nginx_gen.py` `build_context`, enforce regex `^/[a-zA-Z0-9_/-]+/?$` on `route.path`. Reject anything else. Log + skip.
5. Add a smoke test: hit `/admin/` > 60 times/sec from one IP, expect 429.

**Acceptance criteria:**

- `curl -I http://localhost/admin/` includes `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`.
- Rapid-fire `ab -n 200 -c 10 http://localhost/admin/api/health` returns some 429s.
- Submitting a malformed profile nginx_route path is rejected at render time.

**Commit:** `security(nginx): add headers, rate limits, path allowlist (F#24, F#25, F#59)`

---

### T-23: Keycloak — real healthcheck, strict admin password validation, production override

**Priority:** P2
**Addresses:** F#18, F#19
**Effort:** 2 h
**Branch:** `security/T-23-keycloak-hardening`

**Files:**
- `compose/base.yml`
- `compose/overrides/production.yml`
- `.env.example`

**Plan:**

1. **Real healthcheck.** Replace `/bin/true` with:
   ```yaml
   healthcheck:
     test: ["CMD-SHELL", "curl -f http://localhost:8080/keycloak/health/ready || exit 1"]
     interval: 15s
     timeout: 5s
     retries: 20
     start_period: 60s
   ```
   Requires Keycloak's health endpoint to be enabled — add `KC_HEALTH_ENABLED: "true"` to the keycloak service's environment.
2. **Kill the admin/admin default.** Remove `${KEYCLOAK_ADMIN_PASSWORD:-admin}` — use `${KEYCLOAK_ADMIN_PASSWORD:?KEYCLOAK_ADMIN_PASSWORD must be set}`. Docker Compose's `:?` syntax refuses to start if the env var is unset.
3. **Production override:** ensure `compose/overrides/production.yml` runs Keycloak without `start-dev`:
   ```yaml
   keycloak:
     command: ["start", "--optimized", "--http-enabled=false", "--hostname-strict=true"]
   ```
   Document the TLS requirement.
4. Update `.env.example` — remove the implication that admin passwords can be omitted.

**Acceptance criteria:**

- `docker compose -f compose/base.yml up -d` without `KEYCLOAK_ADMIN_PASSWORD` fails with a clear error.
- Healthcheck correctly reflects readiness (verify with deliberately slow Keycloak boot).

**Commit:** `security(keycloak): real healthcheck; refuse default admin password; production override (F#18, F#19)`

---

### T-24: Dockerfiles — non-root, pin base images, multi-stage, .dockerignore

**Priority:** P2
**Addresses:** F#39, F#42, F#43
**Effort:** 1 day
**Branch:** `chore/T-24-dockerfile-hardening`

**Files:**
- Every `services/*/Dockerfile`
- `pipelines/*/Dockerfile`
- New `.dockerignore` at repo root
- `compose/base.yml` and profiles

**Plan:**

1. **Pin base images by digest.** Pick a specific `python:3.11.9-slim-bookworm` digest (run `docker pull python:3.11.9-slim-bookworm && docker inspect python:3.11.9-slim-bookworm --format '{{index .RepoDigests 0}}'`). Use that digest in every Dockerfile. Document the process in CONTRIBUTING.md.
2. **Non-root user.** In each Dockerfile after `WORKDIR /app`:
   ```
   RUN addgroup --system app && adduser --system --ingroup app app
   COPY --chown=app:app ...
   USER app
   ```
   Make sure `mkdir -p /data/*` directories are also `--chown=app:app` or pre-created by the volume mount.
3. **Multi-stage builds.** For each service:
   ```
   FROM python:3.11.9-slim AS builder
   WORKDIR /app
   COPY libs/openhis_sdk /openhis_sdk
   COPY services/<svc>/requirements.txt .
   RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt /openhis_sdk
   
   FROM python:3.11.9-slim
   ...
   COPY --from=builder /wheels /wheels
   RUN pip install --no-cache-dir --find-links=/wheels /wheels/*.whl
   COPY services/<svc> .
   USER app
   ...
   ```
4. **`.dockerignore` at repo root:**
   ```
   .git
   .github
   docs/
   tests/
   *.md
   **/__pycache__/
   **/.pytest_cache
   **/.mypy_cache
   services/_legacy
   venv*
   ```
5. **Verify** image sizes before/after with `docker images`. Target: ≥ 30% reduction on admin.

**Acceptance criteria:**

- Every service Dockerfile has `USER app`.
- `docker run --rm openhis/admin:ci id` shows `uid=<nonzero>`.
- `docker history openhis/admin:ci` is smaller.
- CI build job still passes.

**Commit:** `chore(docker): pin bases, non-root user, multi-stage, .dockerignore (F#39, F#42, F#43)`

---

### T-25: Fix simulator build context + add SDK + ai-controller spawned containers use --security-opt

**Priority:** P2
**Addresses:** F#40, F#41
**Effort:** 2 h
**Branch:** `fix/T-25-simulator-build-context`

**Files:**
- `services/simulator/Dockerfile`
- `services/simulator/main.py`
- `compose/profiles/imaging.yml`
- Replace `${PWD}` with `..` throughout

**Plan:**

1. **Standardize build context.** In `compose/profiles/imaging.yml`, change `build: ../services/simulator` to:
   ```yaml
   build:
     context: ..
     dockerfile: services/simulator/Dockerfile
   ```
2. **Update simulator Dockerfile** to match the other services: copy `libs/openhis_sdk`, install SDK, copy `services/simulator`.
3. **Update simulator `main.py`** to use `openhis_sdk.logging.configure`, `openhis_sdk.auth.JWTMiddleware` with `require_roles("admin")` on `POST /api/generate`. Fix `datetime.now().isoformat()` → `datetime.now(timezone.utc).isoformat()`.
4. **Replace `${PWD}` with `..`** everywhere in `compose/`. Run-from-anywhere compatibility.
5. Re-check T-04 harness covers the simulator now that it has auth.

**Acceptance criteria:**

- `grep -n '\${PWD}' compose/` returns nothing.
- Simulator container imports `openhis_sdk` successfully.
- `curl -X POST http://localhost/simulator/api/generate` without a token returns 401.

**Commit:** `fix(simulator): unify build context; add SDK; require admin role (F#20, F#40, F#41)`

---

### T-26: CI — pip-audit, trivy, and release SBOM + provenance

**Priority:** P2
**Addresses:** F#27, F#56, F#57
**Effort:** 3 h
**Branch:** `ci/T-26-supply-chain`

**Files:**
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/dependabot.yml` — new

**Plan:**

1. **pip-audit** in CI — new job:
   ```yaml
   deps-audit:
     runs-on: ubuntu-latest
     steps:
       - uses: actions/checkout@v4
       - uses: actions/setup-python@v5
         with: { python-version: "3.11" }
       - run: pip install pip-audit
       - run: pip-audit --disable-pip --requirement libs/openhis_sdk/pyproject.toml
       - run: |
           for req in services/*/requirements.txt pipelines/*/requirements.txt platform/requirements.txt; do
             pip-audit -r "$req" || EXIT=1
           done
           exit ${EXIT:-0}
   ```
2. **Trivy** on built images — in the existing `docker-build` job, after build:
   ```yaml
   - uses: aquasecurity/trivy-action@0.24.0
     with:
       image-ref: openhis/${{ matrix.service }}:ci
       severity: CRITICAL,HIGH
       exit-code: '1'
       ignore-unfixed: true
   ```
3. **Release SBOM + provenance.** In `release.yml` `build-push-action`:
   ```yaml
   - uses: docker/build-push-action@v5
     with:
       ...
       provenance: mode=max
       sbom: true
   ```
4. **Dependabot** config `.github/dependabot.yml` for pip + github-actions + docker ecosystems, weekly cadence.

**Acceptance criteria:**

- `pip-audit` CI job runs on every PR.
- `trivy` step flags at least one known vuln if you introduce `requests==2.20.0` temporarily.
- Released images carry signed provenance attestations (verify with `cosign verify-attestation`).

**Commit:** `ci: add pip-audit, trivy, SBOM, provenance, dependabot (F#27, F#56, F#57)`

---

## Phase 5 — Hygiene and cleanup (P3)

Do these last. Safe, low-risk, and mostly cosmetic — but they reduce the cognitive tax on contributors.

---

### T-27: Consolidate compose files and docker-compose duplicates

**Priority:** P3
**Addresses:** F#8, F#41 (cleanup continuation)
**Effort:** 1 h
**Branch:** `chore/T-27-compose-consolidate`

**Plan:**

1. Delete the top-level `docker-compose.yml`. Keep `compose/docker-compose.yml` as the canonical orchestrator.
2. If the top-level `docker-compose.yml` contains anything meaningful not in `compose/`, merge it in first.
3. Update README and Makefile references.
4. Ensure `make up` / `opm up` still work.

**Acceptance criteria:** `ls docker-compose.yml` returns nothing; `make up` works.

**Commit:** `chore: remove duplicate top-level docker-compose.yml (F#8)`

---

### T-28: Archive or remove services/_legacy/

**Priority:** P3
**Addresses:** F#10
**Effort:** 30 min
**Branch:** `chore/T-28-archive-legacy`

**Plan:**

1. Tag `main` at current HEAD as `v0-with-legacy`.
2. `git rm -r services/_legacy/`.
3. Remove `compose/profiles/legacy.yml` if it exists.
4. Update README and CLAUDE.md to remove legacy references. Update the "Repository Layout" sections.

**Acceptance criteria:** `find services -type d -name "_legacy"` returns nothing.

**Commit:** `chore: remove frozen services/_legacy/ (tagged as v0-with-legacy) (F#10)`

---

### T-29: Doc directories, .gitignore, CHANGELOG

**Priority:** P3
**Addresses:** F#5 (cleanup), F#6, F#9, F#77
**Effort:** 1 h
**Branch:** `chore/T-29-docs-cleanup`

**Plan:**

1. Merge `docs/task-planning/` (hyphen, 1 file) into `docs/task_planning/` (underscore, 7 files). Pick one naming and stick.
2. Align `docs/explaining_the_project/` naming (hyphen vs underscore) — pick one, update all references in CLAUDE.md and README.
3. Clean up `.gitignore`: remove the `#tests` commented line and the dangling `scripts` entry.
4. Update `CHANGELOG.md` with all changes from Phases 1–4 (one entry per PR would be ideal, but at minimum a rollup).

**Commit:** `chore(docs): collapse duplicate doc dirs; clean .gitignore (F#6, F#9, F#77)`

---

### T-30: Remove dead SDK code; add request logging where wanted

**Priority:** P3
**Addresses:** F#61
**Effort:** 30 min
**Branch:** `chore/T-30-sdk-cleanup`

**Plan:**

1. If `RequestLoggingMiddleware` is wanted (it's useful for latency visibility), wire it into the SDK bootstrap pattern — i.e., have the standard service template from T-20 call `app.add_middleware(RequestLoggingMiddleware)`.
2. Otherwise delete it.
3. Grep for other unused exports in `openhis_sdk/__init__.py` and prune.

**Commit:** `chore(sdk): wire or remove RequestLoggingMiddleware (F#61)`

---

### T-31: Audit log fallback visibility

**Priority:** P3
**Addresses:** F#75
**Effort:** 30 min
**Branch:** `chore/T-31-audit-fallback-log`

**Plan:**

1. In `services/integration-hub/app/db/audit.py`'s `log_event`: replace `except Exception: pass` with `except Exception as e: log.error("audit write failed: %s", e)`. The stated policy "audit failure must never break the sync path" holds, but the failure becomes visible.
2. Same pattern in any other silent audit-write swallowing.

**Commit:** `chore(audit): log when audit write fails instead of swallowing (F#75)`

---

### T-32: Template the three infra DB passwords

**Priority:** P3
**Addresses:** F#68 (remaining — DB passwords portion)
**Effort:** 2 h
**Branch:** `security/T-32-infra-db-passwords`

**Plan:**

1. Template `db_password` in `odoo.conf.j2` using `ODOO_DB_PASS` from `.env`.
2. Template `connection.password` in `openmrs-runtime.properties.j2` using `OPENMRS_DB_PASS`.
3. Template `datasource.password` in `openelis/common.properties.j2` using `OPENELIS_DB_PASS`.
4. Add these three env vars to `.env.example` and to `opm init`'s `_REQUIRED_SECRETS`.
5. Ensure the corresponding compose services read the matching env var and pass it to the DB initialization.

**Acceptance criteria:**

- `grep -rE "db_password = odoo|connection.password=openmrs|datasource.password=clinlims" infra/ | grep -v '.j2:'` returns nothing.
- Fresh deploy with opm-generated `.env` produces matching DB passwords in all three config files.

**Commit:** `security(infra): template DB passwords from .env (F#68)`

---

## Progress tracker

Check tasks off as they land. Link the PR number after the title.

### Phase 1 — Stop the bleeding (P0)
- [ ] T-01: Delete the dead NJS JWT guard
- [ ] T-02: Lock down ai-controller pipeline registration and runtime
- [ ] T-03: Authenticate all admin routers
- [ ] T-04: Make DEV_MODE opt-in; add auth-enforcing test harness
- [ ] T-05: Add role checks to RIS orders/patients and MPI lookup
- [ ] T-06: Lock down integration-hub event ingest and simulator
- [ ] T-07: Fix BusConsumer ack semantics and add dead-letter path
- [ ] T-08: Remove MLLP host port mapping
- [ ] T-09: Template Keycloak realm secrets at opm init
- [ ] T-10: Fix opm init to cover all secrets

### Phase 2 — Detection infrastructure (P1)
- [ ] T-11: Migrate admin to SDK; fix CI drift regex
- [ ] T-12: Add ruff lint; ban datetime.utcnow; cleanup sweep
- [ ] T-13: Fix CI pytest paths, Makefile, CLAUDE.md refs

### Phase 3 — Correctness (P1)
- [ ] T-14: Fix adapter error handling to match contract
- [ ] T-15: Reconcile adapter contract with reality
- [ ] T-16: Fix MPI matcher (diacritics, threshold ADR)
- [ ] T-17: Fix HL7 parser, Redis pooling, rename token.py, centralize maxlen
- [ ] T-18: Fix FHIR translator edge cases
- [ ] T-19: Fix opm commands (config auth, upgrade wait, timeouts)
- [ ] T-20: Fix opm add-service scaffold
- [ ] T-21: Implement data retention policies

### Phase 4 — Hardening (P2)
- [ ] T-22: nginx security headers + rate limits + path allowlist
- [ ] T-23: Keycloak real healthcheck + admin password enforcement
- [ ] T-24: Dockerfiles — non-root, pin, multi-stage, .dockerignore
- [ ] T-25: Fix simulator build context + add SDK
- [ ] T-26: CI supply chain — pip-audit, trivy, SBOM, provenance

### Phase 5 — Cleanup (P3)
- [ ] T-27: Consolidate compose files
- [ ] T-28: Archive or remove services/_legacy/
- [ ] T-29: Doc directories, .gitignore, CHANGELOG
- [ ] T-30: Remove dead SDK code or wire it
- [ ] T-31: Audit log fallback visibility
- [ ] T-32: Template the three infra DB passwords

---

## Notes on effort

Rough total: 4–6 weeks of focused work for one engineer, or 2–3 weeks split across two.

Phase 1 alone is ~10 days and is the portion that most materially reduces risk. Everything after Phase 1 is about making the project maintainable and making future regressions visible — also important, but not "burn everything down" important.

## Finding-to-task cross reference

For PR bodies, the finding numbers (F#01–F#78) cited in this plan refer to the audit reports. The mapping:

| Phase | Tasks | Findings addressed |
|---|---|---|
| 1 | T-01 to T-10 | F#2, F#15, F#16, F#17, F#20, F#31, F#32, F#33, F#38, F#45–49, F#63–66, F#68, F#78 |
| 2 | T-11 to T-13 | F#1, F#5, F#7, F#11, F#21, F#22, F#62, F#67 |
| 3 | T-14 to T-21 | F#34–37, F#50–55, F#58, F#69–76 |
| 4 | T-22 to T-26 | F#18, F#19, F#24, F#25, F#27, F#39, F#40–43, F#56, F#57, F#59 |
| 5 | T-27 to T-32 | F#6, F#8, F#9, F#10, F#12 (via T-11), F#61, F#68 (DB portion), F#75, F#77 |

Any finding not explicitly mapped above is either (a) a duplicate of another with the same root cause, (b) rolled into a broader task, or (c) deliberately deferred as low-risk. If something important is missing, open an issue referencing the finding number and we'll add a task.
