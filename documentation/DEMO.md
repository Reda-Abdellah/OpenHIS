# OpenHIS Demo Walkthrough

Step-by-step guide to verify every integration path in the stack.
All URLs assume the stack is running on `localhost`.

---

## Prerequisites

```bash
# Build and start the full stack
make up-build

# Watch startup progress (takes ~15 min on first boot for OpenMRS Liquibase)
docker compose logs -f openmrs openelis odoo integration-hub
```

Wait until all health checks turn green:

```bash
make ps   # look for "(healthy)" next to every service
```

---

## 1 — OpenMRS (EHR)

**URL:** http://localhost/openmrs/spa
**Credentials:** `admin / Admin123`

### Register a patient

1. Open the SPA and log in.
2. **Patient list → Register patient**:
   - Name: `Jane Doe`
   - Date of birth: `1985-04-12`
   - Sex: `Female`
   - Identifier: `MRN-001`
3. Save. Copy the patient UUID from the URL bar.

### Verify via FHIR

```bash
curl -s -u admin:Admin123 \
  "http://localhost/openmrs/ws/fhir2/R4/Patient?identifier=MRN-001" \
  | python3 -m json.tool | grep '"id"'
```

---

## 2 — OpenELIS (LIS)

**URL:** http://localhost:8082
**Credentials:** `admin / adminADMIN!`

### Confirm patient synced from OpenMRS

Within one poll cycle (~60 s) the Integration Hub copies new OpenMRS patients
into OpenELIS.  Verify:

```bash
curl -s -u admin:'adminADMIN!' \
  "http://localhost:8082/fhir/R4/Patient?identifier=MRN-001" \
  | python3 -m json.tool | grep '"id"'
```

### Create a lab order in OpenMRS

1. In OpenMRS SPA → patient **Jane Doe** → **Orders** → New lab order.
2. Select a test (e.g. *Complete Blood Count*). Save.

### Check the order appeared in OpenELIS

```bash
curl -s -u admin:'adminADMIN!' \
  "http://localhost:8082/fhir/R4/ServiceRequest?subject=Patient/MRN-001" \
  | python3 -m json.tool
```

### Record and check results

1. In OpenELIS, open the order and enter results. Set status to **Final**.
2. Within the next poll cycle the Integration Hub posts the `DiagnosticReport`
   back to OpenMRS.

```bash
curl -s -u admin:Admin123 \
  "http://localhost/openmrs/ws/fhir2/R4/DiagnosticReport?patient=MRN-001" \
  | python3 -m json.tool
```

---

## 3 — Odoo (ERP / Pharmacy)

**URL:** http://localhost:8069
**Master password:** `admin`

### First-boot setup (one time only)

1. Open http://localhost:8069 — you see the *Create Database* page.
2. Create a database named **`odoo`**, email `admin@openhis.local`, password `admin`.
3. Install modules: **Sale Management**, **Inventory**, **Accounting**.

### Verify via XML-RPC

```bash
python3 - <<'EOF'
import xmlrpc.client
url = "http://localhost:8069"
db, user, pw = "odoo", "admin", "admin"
uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, user, pw, {})
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
partners = models.execute_kw(db, uid, pw, "res.partner", "search_count", [[]])
print(f"Odoo auth OK (uid={uid}), partners={partners}")
EOF
```

---

## 4 — Integration Hub

**Health:** http://localhost/integration-hub/api/health
**Status:** http://localhost/integration-hub/api/atomfeed/status
**Audit:**  http://localhost/integration-hub/api/audit

### Check live status

```bash
curl -s http://localhost/integration-hub/api/atomfeed/status | python3 -m json.tool
```

Expected output:

```json
{
  "patients_synced": 1,
  "orders_synced": 1,
  "reports_synced": 0,
  "errors": 0,
  "last_poll_at": "2026-..."
}
```

### Trigger a manual sync cycle

```bash
curl -s -X POST http://localhost/integration-hub/api/atomfeed/trigger
```

### Inspect audit log

```bash
# Last 10 events
curl -s "http://localhost/integration-hub/api/audit?limit=10" | python3 -m json.tool

# Filter to failures only
curl -s "http://localhost/integration-hub/api/audit?event_type=patient_sync_failed" \
  | python3 -m json.tool
```

---

## 5 — Radiology (RIS → Orthanc → OHIF)

**RIS UI:**      http://localhost/ris
**Orthanc UI:**  http://localhost/orthanc
**OHIF Viewer:** http://localhost/ohif

### Push a DICOM study

1. Open http://localhost/simulator and use the **DICOM Acquisition Simulator**
   to send a study (choose any patient and modality).
2. In the Orthanc UI, confirm the study arrived.

### OpenMRS → RIS order sync

If an imaging `ServiceRequest` was created in OpenMRS for Jane Doe, the RIS
background worker (`openmrs_sync.py`) will auto-create the order within the
next poll cycle.  Check:

```bash
curl -s http://localhost/ris/api/orders | python3 -m json.tool
```

### Generate a report

1. In RIS → **Orders** → open the order for Jane Doe.
2. Click **Create report** → fill in findings → **Finalize**.
3. The RIS POSTs a `report-final` event to `integration-hub`, which creates
   a `DiagnosticReport` in OpenMRS FHIR.

---

## 6 — Patient Portal

**URL:** http://localhost/patient-portal
**Login:** MRN + date of birth (YYYY-MM-DD)

1. Open the patient portal.
2. Log in with `MRN-001` / `1985-04-12`.
3. Navigate tabs: **Results**, **Imaging**, **Diagnoses**, **Appointments**.
4. Lab results posted by Integration Hub appear under **Results**.
5. Finalized radiology reports appear under **Imaging**.

---

## 7 — Analytics Dashboard

**URL:** http://localhost/analytics

The dashboard auto-collects metrics every 5 minutes from OpenMRS, OpenELIS,
and the RIS.  Metrics include:

| Category | Metric |
|----------|--------|
| EHR      | Total patients, encounters today |
| LIS      | Pending orders, completed results |
| Radiology| Studies pushed, reports finalized |
| AI       | Jobs queued, jobs complete |

```bash
curl -s http://localhost/analytics/api/metrics/latest | python3 -m json.tool
```

---

## 8 — HL7 v2 Inbound (MLLP)

**Port:** 2575 (host) → `hl7` container

### Send a test ADT^A01 (patient admit)

```bash
python3 - <<'EOF'
import socket, datetime

now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
msg = (
    f"MSH|^~\\&|SIM|HOSP|OpenHIS|HIS|{now}||ADT^A01|MSG001|P|2.5\r"
    f"EVN|A01|{now}\r"
    f"PID|1||MRN-002^^^HOSP^MR||Smith^John||19700101|M\r"
    f"PV1|1|I|WARD1^101^A|||DRDOE^Doe^Jane\r"
)
MLLP_START, MLLP_END = b"\x0b", b"\x1c\x0d"
frame = MLLP_START + msg.encode() + MLLP_END

s = socket.create_connection(("localhost", 2575), timeout=5)
s.sendall(frame)
ack = s.recv(1024)
s.close()
print("ACK:", ack.decode(errors="replace"))
EOF
```

Verify the patient `MRN-002` appeared in OpenMRS:

```bash
curl -s -u admin:Admin123 \
  "http://localhost/openmrs/ws/fhir2/R4/Patient?identifier=MRN-002" \
  | python3 -m json.tool | grep '"id"'
```

---

## 9 — Full End-to-End Smoke Test

```bash
# Phase 1 verification (OpenMRS health + FHIR)
python scripts/verify_openmrs.py

# Phase 2 verification (OpenELIS health + FHIR)
python scripts/verify_openelis.py

# Phase 3 verification (Odoo health + XML-RPC)
python scripts/verify_odoo.py

# Phase 4 verification (Integration Hub health + sync status)
python scripts/verify_hub.py
```

All checks should print `[PASS]` with exit code 0.

---

## Teardown

```bash
# Stop all services (preserves volumes / data)
make down

# Full wipe including all data volumes
make clean
```
