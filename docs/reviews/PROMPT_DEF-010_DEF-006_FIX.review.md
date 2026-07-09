# Ultrareview Findings — PROMPT_DEF-010_DEF-006_FIX.md

Remote review session: https://claude.ai/code/session_01JsZiy9vFAH8f2GAUiKZsPg
Date: 2026-05-03
Scope: 1 file changed, 179 insertions(+) — `PROMPT_DEF-010_DEF-006_FIX.md`

Two findings; both apply to the prompt itself, not to runtime code. The prompt
is the entire deliverable on this branch and is intended as copy-paste
instructions for an implementing agent. Following it verbatim would either
chase non-existent code, violate hard rules, or silently re-introduce
DEF-010.

---

## Finding 1 — Architecture description is wrong (severity: normal)

**Location:** `PROMPT_DEF-010_DEF-006_FIX.md` lines 22-50

The "Current Architecture" and "Data Flow" sections do not match the code on
this branch on at least four points.

### 1a. Hub already publishes events
- `services/integration-hub/app/worker.py:134` calls
  `bus.publish('patient.registered', {...})` with full demographics after
  every OpenMRS sync.
- Same file also publishes `odoo.patient.synced` (line 150),
  `lab_order.routed` (line 178), `lab_result.ready` (line 208).
- Prompt's repeated claim that the hub "does **not** publish"
  (lines 15, 36, 41, 60-65) is false.

### 1b. `patient.synced` flow is inverted
- `services/mpi/bus_consumer.py:132` declares
  `handlers={'patient.registered': _handle_patient_registered}` — MPI
  subscribes to `patient.registered`, not `patient.synced`.
- After upserting the master record, MPI itself emits `patient.synced` at
  line 118: `await publish_event(_get_redis(), 'patient.synced',
  {master_id, mrn, omrs_id, oe_id})`.
- CLAUDE.md's bus events table lists MPI as the producer of `patient.synced`.
- Prompt says MPI "Subscribes to Redis bus topic `patient.synced`" (line 25)
  and "Publishes `patient.synced` to hub" (line 30) — both inverted.

### 1c. `openmrs.upsert_patient()` does not exist
- `services/integration-hub/app/services/openmrs.py` only exposes:
  `_auth_headers`, `health_check`, `get_recent_patients`,
  `get_active_service_requests`, `post_diagnostic_report`, `find_patient_uuid`.
- MPI record creation is owned by
  `services/mpi/bus_consumer.py:_handle_patient_registered`, not by any hub
  adapter.
- CLAUDE.md hard rules forbid direct HTTP calls between native services, so
  an agent that adds an HTTP-based MPI upsert from the OpenMRS adapter would
  also violate that rule.

### 1d. Hub dedup is Redis-backed, not in-memory
- `worker.py:30-51`: "Redis-backed dedup sets with 7-day TTL — survive
  restarts. Falls back to in-memory sets when REDIS_URL is not configured."
- `_dedup_check()` uses `r.sadd(full_key, value)` followed by
  `r.expire(full_key, _DEDUP_TTL=7*24*3600)`.
- Prompt Notes line 178 flatly states "The hub's dedup uses an in-memory
  set"; example block (line 109) introduces a per-call `synced_set = set()`,
  which would regress the Redis migration.

### 1e. Self-contradiction at line 47
- Step 7 of "Data Flow (Current)" reads
  `Hub publishes patient.synced event ✅ (only MPI publishes this path
  today)` — the ✅ asserts the step happens, the parenthetical asserts it
  doesn't. Likely the "Current" and "Broken" diagram labels were swapped.

### Consequences if implemented as written
1. Agent adds `bus.publish('patient.synced', {mrn, master_id, detail})` to
   the hub. There are now two producers of `patient.synced` with
   incompatible payload schemas (hub: `{mrn, master_id, detail}` with
   `master_id` likely `None` because the hub never knows it; MPI:
   `{master_id, mrn, omrs_id, oe_id}`).
2. Downstream consumers (analytics, hl7) start receiving events with a
   missing/different `master_id` and break.
3. Restart-survivable Redis dedup is replaced by per-process sets; replays
   after a worker restart re-fire bus events.

### Suggested rewrite of "Current Architecture"
- Hub polls OpenMRS → calls `openelis.upsert_patient` and
  `odoo.upsert_patient` (the only `upsert_patient` functions, on those
  external-system adapters), then publishes `patient.registered` with full
  demographics.
- MPI `bus_consumer` consumes `patient.registered`, upserts
  `master_patients` and `cross_references`, and publishes `patient.synced`
  with `{master_id, mrn, omrs_id, oe_id}`.
- Hub dedup is Redis SADD + 7-day TTL via `_dedup_check`; the in-memory
  fallback only triggers when `REDIS_URL` is unset.
- Swap the "Current" and "Broken" diagram labels (or fix the ✅ on line 47
  to ❌).
- If a real DEF-010 still exists, re-diagnose it from the actual flow — it
  is more likely about MPI not consuming `patient.registered` correctly in
  some case than about the hub failing to publish `patient.synced`.

---

## Finding 2 — Example code block is non-functional (severity: nit)

**Location:** `PROMPT_DEF-010_DEF-006_FIX.md` lines 109-125

The "Add Bus Publish to Hub" example pattern (lines 90-138) is presented as
a copy-paste template but cannot be used as-is.

### 2a. Inverted dedup makes `publish()` unreachable
Trace with `patients = [P(uuid='A'), P(uuid='B')]`:

```
synced_set = set()                   # empty
for p in patients:                   # first loop
    if p.uuid not in synced_set:     # True for each unique UUID
        await openmrs_adapter.upsert_patient(p)
        synced_set.add(p.uuid)       # UUID now present
# After loop: synced_set == {'A','B'}

for p in patients:                   # second loop
    if p.uuid not in synced_set:     # ALWAYS False
        await publish(...)           # NEVER runs
```

`publish()` is never invoked — silently re-introduces DEF-010. Correct
logic is to publish inside the first loop right after the successful
`upsert_patient()`, or invert the second-loop guard to
`if p.uuid in synced_set`.

### 2b. Wrong SDK function name and signature
- `libs/openhis_sdk/src/openhis_sdk/bus.py` defines:
  `async def publish_event(client: aioredis.Redis, event_type: str, payload: dict) -> None`
- The example imports `publish` (does not exist in the SDK) and calls it
  with `(event_type, payload, headers={...})` — no client, unsupported
  `headers=` kwarg.
- Real call sites (`services/mpi/bus_consumer.py`,
  `tests/unit/mpi/test_bus_consumer.py`) all use `publish_event`.
- The integration-hub does have a local `from app.bus import publish` with
  signature `publish(event_type, payload, source='integration-hub')` —
  matches the (event_type, payload) shape but also doesn't accept
  `headers=`, so the example breaks under either binding.

### 2c. Non-existent target file
- Prompt directs work to `services/integration-hub/app/bus_adapter.py`
  (line 75 heading and line 158 "Files to Modify").
- Listing `services/integration-hub/app/` shows: `__init__.py, bus.py,
  config.py, db/, jwt_auth.py, log_config.py, main.py, registry.py,
  routers/, services/, state.py, token.py, translators/, utils/, worker.py`.
- No `bus_adapter.py`. The bus module is `app/bus.py` (`publish()` at line
  60) and the polling loop is in `app/worker.py`.

### 2d. Pre-existing (not flagged as a blocker)
- Package name typo `openhissdk` vs on-disk `openhis_sdk` — pre-existing
  project-wide (CLAUDE.md uses the same broken form). Tracked separately,
  not a blocker for this PR.

### Suggested fix for the example block
```python
from app.bus import publish  # local hub bus module
# OR: from openhis_sdk.bus import publish_event with a redis client

async def sync_openmrs_patients():
    patients = await omrs_client.get_patients()
    if not patients:
        return
    synced_set = set()
    for p in patients:
        if p.uuid in synced_set:
            continue
        await openmrs_adapter.upsert_patient(p)
        synced_set.add(p.uuid)
        await publish("patient.synced", {
            "mrn": p.mrn,
            "master_id": openmrs_adapter.get_master_id(p.uuid),
            "detail": "Synced from OpenMRS",
        })
        log.info("published patient.synced", extra={"mrn": p.mrn})
```

Update "Files to Modify" to point at:
- `services/integration-hub/app/worker.py` (where `_sync_patients` lives)
- `services/integration-hub/app/bus.py` (where `publish()` is defined)

---

## Action items

- [ ] Decide whether DEF-010 is a real defect at all, given that the hub
      already publishes `patient.registered` and MPI already publishes
      `patient.synced`. Re-diagnose against the actual flow before any
      implementation work.
- [ ] If DEF-010 stands, rewrite the prompt's "Current Architecture",
      "Data Flow" diagrams, and "Files to Modify" sections against the
      real code paths.
- [ ] Fix the example code block: move `publish()` into the first loop,
      use `publish_event(client, ...)` from `openhis_sdk.bus` (or local
      `app.bus.publish`), drop the unsupported `headers=` kwarg.
- [ ] Resolve the package-name typo (`openhissdk` vs `openhis_sdk`)
      project-wide as a separate task — not in scope for this prompt fix.
