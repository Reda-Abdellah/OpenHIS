# Plan: Test Coverage Audit and Gap-Filling for OpenHIS

## Context

OpenHIS is a multi-service health information platform. All production-readiness work was recently completed (phases 1–5). This plan defines what tests correctly validate the platform's key objectives, audits the existing `tests/` suite against those requirements, and adds targeted tests for every confirmed gap.

**Platform objectives that tests must validate:**
1. **Patient identity integrity** — MPI correctly deduplicates, cross-references, and merges patients
2. **Clinical data flow** — lab orders and results reliably route between OpenMRS ↔ OpenELIS ↔ HL7
3. **Event bus consistency** — all services consume and publish bus events correctly; no dropped or double-processed messages
4. **Retry + audit reliability** — transient failures are retried with correct backoff; every action is audited
5. **AI pipeline triggering** — DICOM and lab events auto-trigger the right pipelines without duplicates
6. **Security baseline** — JWT middleware correctly enforces/bypasses auth based on config
7. **Observability** — platform status and registry endpoints return correct aggregated data

---

## Coverage Audit Summary

| Area | Current Status | Gap |
|------|---------------|-----|
| MPI bus consumer | ✅ Covered | — |
| MPI patient CRUD + merge | ❌ Not tested | Need `test_patients.py` |
| Patient matching algorithm | ❌ Not tested | Need `test_matcher.py` |
| HL7 bus consumer (`lab_result.ready`) | ❌ New file, zero tests | Need `test_bus_consumer.py` |
| AI controller `dicom.stored` handler | ❌ New handler, zero tests | Extend `test_bus_consumer.py` |
| `with_retry` decorator | ❌ New file, zero tests | Need `test_retry.py` |
| Registry loader + platform/status | ❌ New module, zero tests | Need `test_registry.py` |
| Worker `poll_once` + Odoo independence | ❌ Not tested | Need `test_worker.py` |
| JWT middleware | ❌ Zero tests | Need `test_jwt_middleware.py` |
| HL7 parser (existing) | ✅ Covered | — |
| HL7 builder (existing) | ✅ Covered | — |
| Integration-hub health (existing) | ✅ Covered | — |
| Analytics bus consumer (existing) | ✅ Covered | — |
| RIS, patient-portal, admin (existing) | ✅ Covered | — |

---

## Files to Create / Extend

### 1. `tests/mpi/test_matcher.py` — NEW
Tests the Jaro-Winkler patient matching algorithm in `services/mpi/matcher.py`.

**Pattern:** Pure unit tests, no DB or HTTP needed. Import `matcher` directly after adding `MPI_PATH` to `sys.path`.

**Tests to write:**

```
# Scoring
test_exact_mrn_match_returns_1_0          → compute_match_score same MRN → 1.0
test_exact_id_match_returns_1_0           → same id field → 1.0
test_identical_demographics_below_1_0     → no MRN, perfect name+dob+sex → 0.99
test_score_weight_firstname_only          → only firstname match → ~0.25
test_score_weight_lastname_only           → only lastname match → ~0.35
test_score_weight_birthdate               → only birthdate match → 0.30
test_score_weight_sex                     → only sex match → 0.10
test_missing_fields_score_zero            → empty dicts → 0.0
test_normalization_removes_special_chars  → "O'Brien" vs "obrien" → high similarity
test_case_insensitive_matching            → "Smith" vs "SMITH" → 1.0 contribution
test_score_rounded_to_4_decimals          → result always 4 decimal places
test_score_bounded_max_0_99_without_mrn   → no MRN → max 0.99

# Candidate finding
test_find_candidates_above_threshold      → two near-matches above 0.70 returned
test_find_candidates_below_threshold      → 0.69 score excluded
test_find_candidates_excludes_self        → same patient.id not returned
test_find_candidates_sorted_descending    → higher score first
test_find_candidates_empty_pool           → returns []
```

---

### 2. `tests/mpi/test_patients.py` — NEW
Tests the MPI patient REST API in `services/mpi/routers/patients.py`.

**Pattern:** Uses `client` fixture from conftest (already updated for PostgreSQL).

**Tests to write:**

```
# CRUD
test_create_patient_returns_201           → POST /api/patients → 201 + id
test_create_patient_duplicate_mrn_409     → second POST same MRN → 409
test_get_patient_returns_xrefs_and_audit  → GET /api/patients/{id} → xrefs + audit keys
test_get_patient_not_found_404            → unknown id → 404
test_list_patients_default_active_only    → only status=active returned
test_list_patients_search_by_name         → ?q=Smith filters by name
test_update_patient_fields                → PATCH updates firstname
test_update_merged_patient_409            → PATCH on merged record → 409
test_lookup_by_mrn                        → GET /api/patients/lookup?mrn=X → patient
test_lookup_not_found_404                 → GET /api/patients/lookup?mrn=UNKNOWN → 404
test_lookup_by_system_crossref            → GET ?system=openmrs&system_id=X → patient via xref

# Merge
test_merge_transfers_xrefs_to_surviving   → surviving patient gains deprecated's xrefs
test_merge_marks_deprecated_as_merged     → deprecated record status='merged'
test_merge_resolves_match_candidate       → pending match_candidate → confirmed_match
test_merge_same_patient_400              → pid == merge_id → 400
test_merge_nonexistent_patient_404        → unknown merge_id → 404
test_merge_writes_audit_log              → audit_log has 'merged' action
test_update_merged_record_rejected        → PATCH on merged record → 409

# Audit
test_create_patient_writes_audit_log      → audit_log has 'created' entry after POST
```

---

### 3. `tests/hl7/test_bus_consumer.py` — NEW
Tests `services/hl7/bus_consumer.py` — the new HL7 lab result consumer.

**Pattern:** Mirrors `tests/ai-controller/test_bus_consumer.py`. Direct async handler calls with `respx.mock` for HTTP. Uses `client`/`fresh_db` from HL7 conftest.

**Tests to write:**

```
# _extract_oru_fields
test_extract_mrn_from_subject_reference   → subject="Patient/MRN001" → mrn=MRN001
test_extract_order_id_from_identifier     → identifier with "order-id" system → order_id
test_extract_order_id_falls_back_to_id    → no identifier → uses report.id
test_extract_observations_from_contained  → 2 contained Observations → 2 results
test_extract_no_observations_empty_list   → no contained → results=[]
test_extract_value_quantity_fields        → valueQuantity.value + unit extracted
test_extract_interpretation_flag          → interpretation coding code extracted

# _handle_lab_result_ready (with respx mocking)
test_handle_no_oe_id_skips               → payload without oe_id → no DB write
test_handle_openelis_404_skips           → 404 response → no ORU built
test_handle_openelis_unavailable_skips   → connection error → no ORU built
test_handle_no_observations_skips        → empty contained → no DB write
test_handle_valid_report_logs_outbound   → valid report → 1 row in messages table
test_handle_message_type_is_oru_r01      → logged msg_type = 'ORU^R01'
test_handle_direction_is_outbound        → logged direction = 'outbound'
test_handle_multiple_observations        → 3 OBX segments in ORU raw message

# _process_message dispatch
test_process_unknown_event_type_ignored  → unknown type → no DB write
test_process_lab_result_dispatched       → lab_result.ready → handler called

# consume_loop
test_consume_loop_disabled_without_redis → REDIS_URL='' → returns immediately
```

---

### 4. `tests/ai-controller/test_bus_consumer.py` — EXTEND (add new block)
Add tests for `_handle_dicom_stored` in `services/ai-controller/bus_consumer.py`.

**Tests to add:**

```
# _handle_dicom_stored
test_handle_dicom_no_study_uid_skips          → no study_uid → 0 jobs
test_handle_dicom_no_matching_rule_skips      → no dicom rules enabled → 0 jobs
test_handle_dicom_creates_job_when_rule_enabled → enable dicom rule → 1 job, source_type='dicom'
test_handle_dicom_modality_filter_match       → filter modality="CT", event modality="CT" → job created
test_handle_dicom_modality_filter_mismatch    → filter "CT", event "MR" → no job
test_handle_dicom_modality_case_insensitive   → filter "ct", event "CT" → match (uppercase normalized)
test_handle_dicom_dedup_prevents_second_job   → same study_uid twice → only 1 job
test_handle_dicom_failed_job_allows_retry     → existing FAILED job → new job created
test_handle_dicom_patient_id_stored           → patient_id from payload stored in job
```

---

### 5. `tests/integration-hub/test_retry.py` — NEW
Tests `services/integration-hub/app/utils/retry.py`.

**Pattern:** Uses `monkeypatch` to mock `asyncio.sleep` so tests run instantly. Adds `HUB_PATH` to sys.path from conftest.

**Tests to write:**

```
test_success_on_first_attempt_no_retry    → fn succeeds → called once, returns value
test_retry_succeeds_on_second_attempt     → fail once then succeed → called twice
test_all_attempts_fail_raises_last        → 3 fails → raises last exception type
test_max_attempts_1_raises_immediately    → max=1, fail → raises, no sleep
test_backoff_formula_base1               → mock sleep, verify delays: 1.0, 2.0 for base=1
test_backoff_formula_base2               → delays: 2.0, 4.0 for base=2
test_preserves_return_value              → fn returns "hello" → decorator returns "hello"
test_preserves_function_name             → wrapper.__name__ == original name (functools.wraps)
test_correct_attempt_count_in_log        → warning logged with attempt number (caplog)
```

---

### 6. `tests/integration-hub/test_registry.py` — NEW
Tests `services/integration-hub/app/registry.py` and the new health.py endpoints.

**Pattern:** Uses `tmp_path` pytest fixture to create temp manifest dirs. Uses `client` from conftest for endpoint tests.

**Tests to write:**

```
# registry.py (unit)
test_load_reads_json_manifests_from_dir  → 2 valid JSON files → all_services() returns 2
test_load_skips_invalid_json             → 1 valid + 1 malformed → 1 service, warning logged
test_load_missing_dir_returns_empty      → dir doesn't exist → all_services() = []
test_all_services_returns_copy           → mutation of result doesn't affect internal list
test_get_service_by_name                 → get_service("mpi") returns mpi manifest
test_get_service_unknown_returns_none    → get_service("nonexistent") → None
test_load_idempotent                     → load() twice → correct count (no doubling)

# GET /api/registry endpoint
test_registry_endpoint_returns_services  → GET /api/registry → {"services": [...]}
test_registry_endpoint_200              → status 200

# GET /api/platform/status endpoint (with respx)
test_platform_status_all_up             → all probes return 200 → status="ok"
test_platform_status_one_down           → one probe fails → status="degraded"
test_platform_status_timeout            → one probe times out (5s) → that service "down"
test_platform_status_includes_latency   → response includes latency_ms for each service
test_platform_status_empty_registry     → no services registered → services=[], status="ok"
test_platform_status_non_200_degraded   → probe returns 500 → service status="degraded"
```

---

### 7. `tests/integration-hub/test_worker.py` — NEW
Tests the polling worker in `services/integration-hub/app/worker.py`.

**Pattern:** Uses `monkeypatch` to mock `openmrs`, `openelis`, `odoo` service calls and the `bus.publish` / `audit.log_event` functions. All mocks are `AsyncMock`. No real HTTP.

**Tests to write:**

```
# _sync_patients — Odoo independence (Phase 5.2)
test_odoo_failure_does_not_block_openelis_sync
  → openelis succeeds, odoo raises → count=1, openelis audit logged "ok", odoo audit logged "failed"

test_openelis_failure_does_not_block_odoo_sync
  → openelis raises, odoo succeeds → odoo audit logged "ok", retry scheduled for openelis

test_both_succeed_both_audited
  → both succeed → audit has omrs→oe "ok" and omrs→odoo "ok"

# _sync_patients — dedup
test_patient_synced_twice_in_same_cycle_skips_second
  → same omrs_id in _synced_patients → second iteration skipped

# Retry queue
test_schedule_retry_queues_item             → _schedule_retry adds to _retry_queue
test_drain_retries_skips_not_due            → item due in future → queue unchanged
test_drain_retries_processes_due_item       → item due now → coro_factory called
test_drain_retries_reschedules_on_failure   → factory raises (attempt<max) → re-queued with incremented attempt
test_drain_retries_exhausted_at_max_attempts → attempt==max → error counter incremented, not re-queued
test_backoff_timing                         → attempt 1 → backoff=15s; attempt 3 → backoff=60s

# poll_once summary
test_poll_once_returns_summary_dict         → runs all steps → returns {patients, orders, results, retried}
test_poll_once_patient_failure_does_not_stop_orders → openmrs patients raises → orders still synced

# Audit
test_patient_sync_writes_audit_ok          → openelis succeeds → audit.log_event called with "patient_synced"
test_patient_sync_failure_writes_audit     → openelis raises → audit.log_event called with "patient_sync_failed"
```

---

### 8. `tests/integration-hub/test_jwt_middleware.py` — NEW
Tests `services/integration-hub/app/jwt_auth.py` JWTMiddleware behavior.

**Pattern:** Uses `client` from conftest; monkeypatches `KEYCLOAK_URL` and `REQUIRE_JWT` in the jwt_auth module.

**Tests to write:**

```
test_health_always_passes_without_token
  → GET /api/health with no auth → 200 (exempt path)

test_middleware_inactive_when_require_jwt_false
  → REQUIRE_JWT=false → GET /api/registry without token → 200

test_middleware_inactive_when_keycloak_url_empty
  → KEYCLOAK_URL="" → GET /api/registry without token → 200

test_middleware_active_missing_token_returns_401
  → KEYCLOAK_URL set + REQUIRE_JWT=true + no header → 401

test_middleware_active_non_bearer_returns_401
  → Authorization: Basic ... → 401

test_middleware_active_invalid_token_returns_401
  → Authorization: Bearer invalid → 401

test_middleware_active_valid_token_passes
  → valid JWT (mocked JWKS validation) → 200

test_docs_path_always_exempt
  → GET /docs → not blocked even when REQUIRE_JWT=true
```

---

## Critical File Paths

| File to Create/Extend | Service Source Under Test |
|---|---|
| `tests/mpi/test_matcher.py` | `services/mpi/matcher.py` |
| `tests/mpi/test_patients.py` | `services/mpi/routers/patients.py` |
| `tests/hl7/test_bus_consumer.py` | `services/hl7/bus_consumer.py` |
| `tests/ai-controller/test_bus_consumer.py` (extend) | `services/ai-controller/bus_consumer.py` |
| `tests/integration-hub/test_retry.py` | `services/integration-hub/app/utils/retry.py` |
| `tests/integration-hub/test_registry.py` | `services/integration-hub/app/registry.py`, `app/routers/health.py` |
| `tests/integration-hub/test_worker.py` | `services/integration-hub/app/worker.py` |
| `tests/integration-hub/test_jwt_middleware.py` | `services/integration-hub/app/jwt_auth.py` |

**Key conftest files (patterns to follow):**
- `tests/mpi/conftest.py` — PostgreSQL drop/recreate, REDIS_URL disabled
- `tests/hl7/conftest.py` — SQLite fresh_db, OPENMRS_URL unreachable, MLLP_ENABLED=false
- `tests/ai-controller/conftest.py` — SQLite fresh_db, REDIS_URL disabled
- `tests/integration-hub/conftest.py` — monkeypatch env vars, tmp_path for audit DB, POLL_INTERVAL_S=99999

**Key existing patterns to reuse:**
- `AsyncMock` + `patch` for mocking service calls in worker tests
- `respx.mock` context manager for HTTP mocking in HL7 bus consumer tests
- `pytest.mark.asyncio` for direct async handler calls
- Direct `_handle_*()` function calls to test consumers without Redis

---

## Verification

After implementation, run:
```bash
# All new tests pass individually
pytest tests/mpi/test_matcher.py -v
pytest tests/mpi/test_patients.py -v
pytest tests/hl7/test_bus_consumer.py -v
pytest tests/ai-controller/test_bus_consumer.py -v -k "dicom"
pytest tests/integration-hub/test_retry.py -v
pytest tests/integration-hub/test_registry.py -v
pytest tests/integration-hub/test_worker.py -v
pytest tests/integration-hub/test_jwt_middleware.py -v

# Full suite (requires local PostgreSQL for MPI tests)
pytest tests/ -v --ignore=tests/integration -q
```

MPI tests require:
```bash
# PostgreSQL must be running with test DB
MPI_DATABASE_URL=postgresql://mpi:mpi@localhost:5432/mpi_test pytest tests/mpi/ -v
```
