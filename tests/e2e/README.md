# OpenHIS — End-to-End Verification & Validation Suite

Executable mirror of [`docs/verification_and_validation/v-and-v-scenario.md`](../../docs/verification_and_validation/v-and-v-scenario.md).

These tests walk real clinical workflows against a **running** stack (`make up`).
They are the regression net for "I fixed service X and broke service Y."

| Scenario | File | Purpose |
|---|---|---|
| S1 | [`test_s01_patient_identity.py`](test_s01_patient_identity.py) | Patient registration & cross-system identity via MPI |
| S2 | [`test_s02_lab_flow.py`](test_s02_lab_flow.py) | Lab order & result routing through the integration hub |
| S3 | [`test_s03_dicom_imaging.py`](test_s03_dicom_imaging.py) | Simulator → Orthanc → hub → OpenMRS → AI pipeline |
| S4 | [`test_s04_sso_rbac.py`](test_s04_sso_rbac.py) | Keycloak SSO + role-gated endpoints |
| S5 | [`test_s05_admin_plane.py`](test_s05_admin_plane.py) | Admin dashboard: registry, audit, events, profiles, topology |
| S6 | [`test_s06_resilience.py`](test_s06_resilience.py) | Redis AOF + service restart persistence (requires docker) |
| S7 | [`test_s07_hl7.py`](test_s07_hl7.py) | HL7 v2: HTTP send, history, stats, MLLP socket |
| S8 | [`test_s08_analytics.py`](test_s08_analytics.py) | Analytics KPIs, trends, export |
| S9 | [`test_s09_patient_portal.py`](test_s09_patient_portal.py) | Patient Portal card → SPA → public auth surface |

## Running

The suite is opt-in — it will not run during the normal unit/integration
sweep. Choose whichever is convenient:

```bash
pytest tests/e2e --e2e                # explicit flag
OPENHIS_E2E=1 pytest tests/e2e        # via env var
make e2e                              # Makefile target (see root Makefile)
```

Pre-requisites:

1. The stack must be up and healthy: `make up && make health`.
2. Keycloak master admin credentials default to `admin/admin`. Override via
   `KEYCLOAK_MASTER_USER` / `KEYCLOAK_MASTER_PASS` if your stack was
   initialised differently.
3. Scenario 6 (resilience) requires `docker ps` without sudo — add your
   user to the `docker` group or run the suite as root. When docker is
   not reachable, the affected steps cleanly skip with a clear message.

## How it behaves by design

- **Live-stack probe**: the session auto-skips if `http://localhost/health`
  is not 200. You'll see *skipped* for every test rather than a pile of
  false-positive failures.
- **Auto-provisioning**: on first run, the suite creates three service-account
  clients in Keycloak — `e2e-test-sa` (all roles), `e2e-noauth-sa` (no
  roles) and `e2e-patient-sa` (`patient` role only, for clinical-gate 403
  tests) — plus the protocol mappers the platform expects
  (`realm-roles` + `openhis-platform` audience). Subsequent runs reuse them.
- **Fresh MRNs per test**: the `fresh_mrn` fixture returns a UUID-scoped
  `E2E-…` string; there is no DELETE route on `/mpi/api/patients`, so the
  suite reports how many `E2E-*` rows are left at session end but does not
  (cannot) purge them.
- **Known defects use `@pytest.mark.xfail(reason="DEF-XXX", strict=False)`**.
  When the defect is fixed, the test auto-promotes to PASSED and surfaces
  an `XPASS` — that's the signal to remove the xfail.

## Current state on the `working_on_MPI` branch

| Result | Count | Meaning |
|---|---|---|
| PASSED  | 53 | Behaviour verified end-to-end |
| XFAILED | 9 | Blocked by known defect (DEF-002, 006, 007, 008) or by the missing OpenMRS-resident demo identity (S9.9) — DEF-001 is fixed and its xfail markers removed; refresh this tally on the next live run — see [test-defect-report-2026-04-14.md](../../docs/task_planning/test-defect-report-2026-04-14.md) |
| SKIPPED | 3  | Scenario 6 — no docker access |
| XPASSED | 0  | (zero is the goal — an XPASS means a defect was fixed; remove the xfail marker) |

Total runtime: ~13 seconds against a running stack.

## Writing new scenarios

1. Pick the next free `S*` number and add a file `test_s0X_<slug>.py`.
2. Put `pytestmark = pytest.mark.e2e` at module scope.
3. One class per logical grouping; each `test_sN_M_<what>` maps to a step in
   the markdown spec.
4. Use the existing fixtures (`mpi_api`, `hub_api`, `admin_api`, `hl7_api`,
   `ris_api`, `orthanc`, `simulator_api`, `ai_api`, `analytics_api`).
5. For known-bad behaviour, `@pytest.mark.xfail(reason="DEF-NNN: …", strict=False)`.
6. Update the companion markdown `v-and-v-scenario.md` so humans and CI see
   the same steps.

## Fixtures quick reference

| Fixture | Scope | What you get |
|---|---|---|
| `admin_token` | session | JWT string — all roles + admin |
| `noauth_token` | session | JWT string — no roles (for 403 tests) |
| `patient_token` | session | JWT string — `patient` role only (for clinical-gate 403 tests, e.g. S4.9) |
| `auth_hdrs` | function | `{"Authorization": "Bearer …"}` for httpx |
| `http` | function | `httpx.Client` against the portal root |
| `mpi_api` / `admin_api` / `hub_api` / `hl7_api` / `ris_api` / `ai_api` / `analytics_api` / `simulator_api` | function | `httpx.Client` rooted at the given service's `/api` prefix |
| `portal_api` | function | `httpx.Client` rooted at `/patient-portal/api` (no Bearer header — portal owns its own session model) |
| `orthanc` | function | `httpx.Client` against `/orthanc` (sends the admin bearer token — the nginx njs guard gates `/orthanc/` behind `auth_request /_auth/radiologist`) |
| `fresh_mrn` | function | New `E2E-XXXXXXXXXX` MRN |
| `docker_available` | session | `bool` — True iff `docker ps` works without sudo |
| `wait_for_event(admin_client, type, *, timeout, since_id)` | helper | Poll `/admin/api/events/recent` |
