# OpenHIS Adapter Contract

An **adapter** connects an external system (OpenMRS, OpenELIS, Odoo, a legacy HIS) to the OpenHIS event bus and/or the MPI. Adapters live in `services/integration-hub/app/services/`.

---

## 1. Purpose

Adapters are thin translation layers. They:

- Speak the external system's protocol (FHIR R4, REST, XML-RPC, HL7, SOAP).
- Convert incoming data to the canonical OpenHIS event payload schema.
- **Do not** contain business logic — routing and retry are handled by `worker.py`.

---

## 2. File layout

```
services/integration-hub/app/services/
├── openmrs.py      # OpenMRS FHIR R4 adapter
├── openelis.py     # OpenELIS FHIR R4 adapter
├── odoo.py         # Odoo XML-RPC adapter
└── my_system.py    # New adapter goes here
```

---

## 3. Required functions

Each adapter module must expose at minimum the functions that `worker.py` calls. Use `async def` and `httpx.AsyncClient`.

```python
import httpx
from app.config import MY_SYSTEM_URL, MY_SYSTEM_USER, MY_SYSTEM_PASS

async def get_recent_patients() -> list[dict]:
    """Return FHIR Patient resources modified in the last poll window."""
    ...

async def upsert_patient(patient: dict) -> str | None:
    """
    Create or update a patient in the target system.
    Returns the target system's patient ID on success, None on skip.
    """
    ...
```

Add any additional functions for orders, results, or documents following the same async pattern.

---

## 4. Configuration

Add adapter config to `app/config.py`:

```python
MY_SYSTEM_URL  = os.environ.get("MY_SYSTEM_URL",  "http://my-system:8080")
MY_SYSTEM_USER = os.environ.get("MY_SYSTEM_USER", "admin")
MY_SYSTEM_PASS = os.environ.get("MY_SYSTEM_PASS", "admin")
```

Inject these in `compose/base.yml` or the relevant profile file.

---

## 5. Event payload schema

After a successful sync, the worker publishes to the bus. Payloads must be flat dicts of strings/numbers — no nested objects except where unavoidable.

| Event type          | Required payload fields |
|---------------------|------------------------|
| `patient.synced`    | `omrs_id`, `oe_id`, `mrn` |
| `lab_order.routed`  | `omrs_id`, `oe_id` |
| `lab_result.ready`  | `oe_id`, `subject` |
| `imaging.completed` | `study_uid`, `patient_id` |

Add new event types by publishing from `worker.py` after the adapter call succeeds. The analytics consumer records all types automatically — no changes needed there.

---

## 6. Error handling

Adapters must raise exceptions on failure. `worker.py` catches these, logs the audit event, and places the item on the retry queue. Adapters must **not** swallow errors silently.

```python
async def upsert_patient(patient: dict) -> str | None:
    async with httpx.AsyncClient() as client:
        r = await client.post(...)
        r.raise_for_status()   # let worker.py handle failures
        ...
```

---

## 7. Testing

Integration tests live in `tests/integration/`. Each adapter must have at least one test that uses a real (or docker-based) instance of the external system — no mocks at the adapter boundary.
