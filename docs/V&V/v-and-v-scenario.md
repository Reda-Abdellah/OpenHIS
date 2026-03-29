<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# Can you create a V\&V scenario that is meant to be followed to verify end-to-end features and interoperability

Based on my deep analysis of the OpenHIS codebase, here is a complete Verification \& Validation scenario document covering every integration boundary in the system.

***

# OpenHIS — Verification \& Validation Scenario Guide

**Version:** 1.0 · **Target Stack:** `base` + `emr` + `laboratory` + `imaging` + `analytics` profiles

***

## How to Use This Document

Each scenario is **self-contained** and follows this structure: **Prerequisites → Test Data → Steps → Expected Signals → Pass Criteria → Rollback**. Scenarios build on each other — run them in order for a full integration sweep. Each step has a ✅ **PASS** and ❌ **FAIL** signal so a tester can work without deep system knowledge.

***

## Environment Setup

Before running any scenario, verify the stack is clean and healthy.

### Stack Bootstrap

```bash
# 1. Initialize configuration
python platform/opm.py init \
  --postgres-pass TestPass123 \
  --admin-pass AdminPass123 \
  --keycloak-pass KeycloakPass123

# 2. Enable all profiles under test
python platform/opm.py enable emr
python platform/opm.py enable laboratory
python platform/opm.py enable imaging
python platform/opm.py enable analytics

# 3. Bring up the full stack
make up

# 4. Wait for all services to be healthy (≈ 90s)
make health
```


### Pre-flight Health Check

| Service | URL | Expected Response |
| :-- | :-- | :-- |
| Admin UI | `http://localhost/admin` | Login page loads |
| Keycloak | `http://localhost/auth` | Welcome page |
| OpenMRS | `http://localhost/openmrs` | Login page |
| OpenELIS | `http://localhost/openelis` | Login page |
| Orthanc | `http://localhost/orthanc` | Orthanc Explorer |
| OHIF Viewer | `http://localhost/ohif` | Viewer loads |
| MPI API | `http://localhost/mpi/api/health` | `{"status":"ok"}` |
| Integration Hub | `http://localhost/hub/api/health` | `{"status":"ok"}` |
| HL7 MLLP | TCP port `2575` | Connection accepted |

✅ **PASS:** All 9 checks return expected responses within 5 seconds.
❌ **FAIL:** Any service returns non-2xx or connection refused — run `docker compose logs <service>` before proceeding.

***

## SCENARIO 1 — Patient Registration \& Cross-System Identity

**Purpose:** Verify that registering a patient in OpenMRS propagates their identity to the MPI and all connected subsystems.

**Covers:** OpenMRS → Integration Hub → MPI → OpenELIS → Odoo (ERP)

### Test Data

```
Patient Name:    Jean-Pierre Durand
Date of Birth:   1978-04-15
Sex:             Male
National ID:     FR-TEST-00001
Phone:           +33 6 12 34 56 78
Address:         12 Rue de Rivoli, Lyon, 69001
```


### Steps

**Step 1.1 — Register patient in OpenMRS**

1. Navigate to `http://localhost/openmrs` → log in as `admin / AdminPass123`
2. Go to **Register a Patient**
3. Fill in all test data fields above
4. Submit and record the assigned **OpenMRS UUID** (format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)

✅ **PASS:** Patient record created, UUID visible in the URL bar.
❌ **FAIL:** Form submission error or duplicate patient warning.

***

**Step 1.2 — Verify MPI cross-reference (within 30s)**

```bash
curl -s http://localhost/mpi/api/patients?national_id=FR-TEST-00001 | python -m json.tool
```

✅ **PASS:** Response contains:

```json
{
  "openmrs_id": "<UUID from Step 1.1>",
  "national_id": "FR-TEST-00001",
  "name": "Jean-Pierre Durand",
  "dob": "1978-04-15"
}
```

❌ **FAIL:** Empty response or 404 — check `docker compose logs integration-hub` for sync errors.

***

**Step 1.3 — Verify patient appeared in OpenELIS**

1. Navigate to `http://localhost/openelis` → log in as `admin / adminADMIN!`
2. Go to **Patient Management → Search**
3. Search by last name `Durand`

✅ **PASS:** Patient record exists with matching DOB and sex.
❌ **FAIL:** Patient not found — check that the `openelis_id` field is populated in the MPI cross-reference:

```bash
curl http://localhost/mpi/api/crossref/<openmrs_uuid>
```


***

**Step 1.4 — Verify MPI cross-reference is complete**

```bash
curl -s http://localhost/mpi/api/crossref/<openmrs_uuid> | python -m json.tool
```

✅ **PASS:** Response contains both `openmrs_id` AND `openelis_id` (non-null).
❌ **FAIL:** `openelis_id` is null — the adapter write-back is missing (see OBJ 4.4 in the to-do list).

***

**Step 1.5 — Verify Admin audit log**

1. Navigate to `http://localhost/admin` → log in
2. Go to **Audit Log**
3. Filter by **Action:** `patient.synced`

✅ **PASS:** One audit entry exists with the correct patient UUID, actor `integration-hub`, and outcome `success`.
❌ **FAIL:** No audit entry — check `AUDIT_LOG_ENABLED` env var in `integration-hub`.

***

## SCENARIO 2 — Laboratory Order Flow

**Purpose:** Verify a full lab cycle: order created in OpenMRS → routed to OpenELIS → result entered → result delivered back as FHIR DiagnosticReport.

**Depends on:** Scenario 1 completed (patient must exist).

**Covers:** OpenMRS → Integration Hub (lab router) → OpenELIS → Redis bus → Integration Hub (result ingester) → FHIR

### Steps

**Step 2.1 — Create a lab order in OpenMRS**

1. In OpenMRS, navigate to the patient record for **Jean-Pierre Durand**
2. Go to **Orders → Lab Order**
3. Order: `Complete Blood Count (CBC)`, urgency: `Routine`
4. Save and record the **Order UUID**

✅ **PASS:** Order saved, status shown as `ACTIVE`.

***

**Step 2.2 — Verify order appeared in OpenELIS (within 60s)**

1. In OpenELIS, go to **Order Management → Pending Orders**
2. Search by patient name `Durand`

✅ **PASS:** CBC order appears with status `Pending`.
❌ **FAIL:** Order missing — check bus event:

```bash
docker exec openhis-redis redis-cli XRANGE openhis.events - + COUNT 20 | grep laborder
```

If no event, the integration-hub adapter failed to publish `laborder.routed`.

***

**Step 2.3 — Enter lab results in OpenELIS**

1. Click the pending CBC order
2. Enter the following results:
| Test | Value | Unit | Flag |
| :-- | :-- | :-- | :-- |
| WBC | 11.2 | 10³/µL | H |
| RBC | 4.8 | 10⁶/µL | Normal |
| Hemoglobin | 14.2 | g/dL | Normal |
| Hematocrit | 42.1 | % | Normal |
| Platelets | 310 | 10³/µL | Normal |

3. Set status to **Validated** and save.

✅ **PASS:** Results saved, order status changes to `Completed`.

***

**Step 2.4 — Verify FHIR DiagnosticReport (within 30s)**

```bash
curl -s "http://localhost/hub/fhir/DiagnosticReport?patient=<openmrs_uuid>&_sort=-date" \
  -H "Accept: application/fhir+json" | python -m json.tool
```

✅ **PASS:** Response is a FHIR `Bundle` containing a `DiagnosticReport` with:

- `status: final`
- `subject.reference` matching the patient UUID
- 5 `result` entries (one per CBC component)
- `conclusion` or `interpretation` present on the WBC entry (flagged High)

❌ **FAIL:** Empty bundle — check `labresult.ready` event was published:

```bash
docker exec openhis-redis redis-cli XRANGE openhis.events - + COUNT 50 | grep labresult
```


***

**Step 2.5 — Verify result visible in OpenMRS**

1. In OpenMRS, navigate back to the patient record
2. Go to **Results / Observations**

✅ **PASS:** CBC results displayed in the patient chart with the flagged WBC value highlighted.

***

**Step 2.6 — Verify HL7 ORU^R01 emission (if downstream configured)**

```bash
docker compose logs hl7 | grep "ORU\^R01" | tail -5
```

✅ **PASS:** Log line contains `Sent ORU^R01 for patient <openmrs_uuid>` with a `MSH` segment timestamp.
❌ **FAIL (non-blocking):** If no downstream MLLP target is configured, verify the consumer at least *received* the event:

```bash
docker compose logs hl7 | grep "labresult.ready"
```


***

## SCENARIO 3 — DICOM Imaging Workflow

**Purpose:** Verify a radiology order flows from OpenMRS → RIS → Orthanc DICOM store → OHIF viewer, and the study is accessible to the AI pipeline.

**Depends on:** Scenario 1 completed.

**Covers:** OpenMRS → Integration Hub → RIS → Orthanc → Redis bus → AI Controller → OHIF

### Prerequisites

```bash
# Download a public-domain DICOM test file (chest X-ray)
curl -L "https://www.rubomedical.com/dicom_file/0002.DCM" -o /tmp/test_chest.dcm

# Confirm Orthanc is reachable
curl -s http://localhost/orthanc/system | python -m json.tool
```


### Steps

**Step 3.1 — Create radiology order in OpenMRS**

1. On the patient record for **Jean-Pierre Durand**
2. Go to **Orders → Radiology Order**
3. Modality: `CR` (Computed Radiography), Body Part: `Chest`, Laterality: `PA`
4. Urgency: `Routine`. Save and record **Order UUID**.

✅ **PASS:** Order saved, status `ACTIVE`.

***

**Step 3.2 — Verify RIS worklist entry (within 30s)**

```bash
curl -s http://localhost/ris/api/worklist | python -m json.tool
```

✅ **PASS:** Response contains a worklist entry with:

- `patient_name: "Durand^Jean-Pierre"`
- `modality: "CR"`
- `status: "SCHEDULED"`

❌ **FAIL:** Empty worklist — check `docker compose logs ris` and verify `laborder.routed` event was consumed.

***

**Step 3.3 — Push a DICOM study to Orthanc**

Use the RIS-assigned Study Instance UID from Step 3.2 output:

```bash
# Get the Study Instance UID from the worklist
STUDY_UID=$(curl -s http://localhost/ris/api/worklist | python -c "import sys,json; print(json.load(sys.stdin)[^0]['study_uid'])")

# Push the test DICOM file to Orthanc
curl -s -X POST http://localhost/orthanc/instances \
  -H "Content-Type: application/dicom" \
  --data-binary @/tmp/test_chest.dcm
```

Record the returned **Orthanc Instance ID**.

✅ **PASS:** Response contains `{"ID": "<orthanc-uuid>", "Status": "Success"}`.
❌ **FAIL:** 400 or 415 error — verify Orthanc storage plugin is running:

```bash
curl http://localhost/orthanc/plugins
```


***

**Step 3.4 — Verify `dicom.stored` event published (within 15s)**

```bash
docker exec openhis-redis redis-cli XRANGE openhis.events - + COUNT 50 | grep dicom.stored
```

✅ **PASS:** Event entry present with `studyuid`, `patientid`, and `modality` fields.
❌ **FAIL:** Event missing — the Orthanc webhook to integration-hub is not firing. Check:

```bash
curl http://localhost/orthanc/changes?last=0&limit=5
docker compose logs integration-hub | grep "orthanc"
```


***

**Step 3.5 — Verify study visible in OHIF Viewer**

1. Navigate to `http://localhost/ohif`
2. Search by patient name `Durand`

✅ **PASS:** Study appears in the worklist. Click to open — image renders in the viewer.
❌ **FAIL:** Study not listed — check DICOMweb WADO-RS routing:

```bash
curl "http://localhost/orthanc/wado?requestType=WADO&studyUID=$STUDY_UID" -I
```


***

**Step 3.6 — Verify AI pipeline triggered (within 60s)**

```bash
docker compose logs ai-controller | grep "inference" | tail -5
```

✅ **PASS:** Log line shows `Triggered inference job for study <STUDY_UID> pipeline poc-xray`.
❌ **FAIL (non-blocking at current stage):** If `ai-controller`'s bus consumer is not yet implemented, verify the event was at least received:

```bash
docker compose logs ai-controller | grep "dicom.stored"
```


***

**Step 3.7 — Verify RIS report creation**

1. In OpenMRS, navigate to the patient orders
2. The radiology order status should have updated to `IN_PROGRESS` (or `COMPLETED` if report was filed)
```bash
# Check RIS report endpoint
curl -s "http://localhost/ris/api/reports?patient_id=<openmrs_uuid>" | python -m json.tool
```

✅ **PASS:** Report entry exists with `study_uid` matching Step 3.3 and `status: draft` or `final`.

***

## SCENARIO 4 — SSO \& Role-Based Access Control

**Purpose:** Verify that Keycloak SSO tokens are enforced across all native services, and that roles restrict access correctly.

**Covers:** Keycloak → Admin → MPI → Integration Hub → RIS → Analytics

### Test Users

Create these users in Keycloak at `http://localhost/auth` → Realm `openhis`:


| Username | Password | Realm Role | Expected Access |
| :-- | :-- | :-- | :-- |
| `dr.martin` | `DrPass123!` | `clinician` | OpenMRS, OHIF, patient portal |
| `lab.tech` | `LabPass123!` | `laboratory` | OpenELIS, lab orders |
| `radiologist` | `RadPass123!` | `radiologist` | RIS, OHIF, Orthanc |
| `sysadmin` | `SysPass123!` | `admin` | All services + Admin UI |
| `readonly` | `ReadPass123!` | (no role) | No access to any protected API |

### Steps

**Step 4.1 — Token acquisition**

```bash
# Get a token for dr.martin
TOKEN=$(curl -s -X POST \
  "http://localhost/auth/realms/openhis/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=openhis-client&username=dr.martin&password=DrPass123!" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token acquired: ${TOKEN:0:50}..."
```

✅ **PASS:** Token string printed, not empty.

***

**Step 4.2 — Authorized access to MPI (clinician role)**

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost/mpi/api/patients?national_id=FR-TEST-00001" | python -m json.tool
```

✅ **PASS:** Patient record returned (200 OK).

***

**Step 4.3 — Unauthorized access (no-role user)**

```bash
# Get token for readonly user
READONLY_TOKEN=$(curl -s -X POST \
  "http://localhost/auth/realms/openhis/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=openhis-client&username=readonly&password=ReadPass123!" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $READONLY_TOKEN" \
  "http://localhost/mpi/api/patients?national_id=FR-TEST-00001"
```

✅ **PASS:** Response is `403` (forbidden, not 200 or 401).
❌ **FAIL:** `200` returned — the JWT fail-open bug is present (see OBJ 1.2 in the to-do list).

***

**Step 4.4 — No token at all**

```bash
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/mpi/api/patients?national_id=FR-TEST-00001"
```

✅ **PASS:** Response is `401`.
❌ **CRITICAL FAIL:** `200` returned — the service is entirely unprotected.

***

**Step 4.5 — Admin UI restricted to admin role**

1. Open `http://localhost/admin` in a browser
2. Log in with `dr.martin / DrPass123!`

✅ **PASS:** Login fails with "Insufficient permissions" or redirects to Keycloak with an access_denied error.
❌ **FAIL:** Admin UI loads for a non-admin user.

***

**Step 4.6 — Token expiry behavior**

```bash
# Use an expired or malformed token
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.EXPIRED.SIGNATURE" \
  "http://localhost/mpi/api/patients?national_id=FR-TEST-00001"
```

✅ **PASS:** Response is `401`.
❌ **FAIL:** Response is `200` or `500` — JWT validation is not handling malformed tokens.

***

## SCENARIO 5 — Admin Plane \& Observability

**Purpose:** Verify the Admin service accurately reflects system state, that profiles can be toggled, and that the audit trail is consistent.

**Covers:** Admin service → Registry → Profile Engine → Topology API

### Steps

**Step 5.1 — Service registry completeness**

```bash
curl -s http://localhost/admin/api/services \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool
```

✅ **PASS:** Response lists all active services with `status: online` for: `mpi`, `integration-hub`, `hl7`, `ris`, `analytics`, `ai-controller`, `patient-portal`.
❌ **FAIL:** Any active service shows `status: offline` — check the service's `/api/health` endpoint directly.

***

**Step 5.2 — Topology graph integrity**

```bash
curl -s http://localhost/admin/api/topology \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool
```

✅ **PASS:** Response contains:

- `nodes` array with one entry per registered service
- `edges` array showing connections (e.g., `integration-hub → mpi`, `hl7 → integration-hub`)
- No orphan nodes (nodes with zero edges)

***

**Step 5.3 — Profile disable and re-enable**

```bash
# Disable analytics profile via API
curl -s -X POST http://localhost/admin/api/profiles/analytics/disable \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool

# Wait 10s, then verify analytics service is gone from registry
sleep 10
curl -s http://localhost/admin/api/services \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -c \
  "import sys,json; svcs=[s['name'] for s in json.load(sys.stdin)]; print('PASS' if 'analytics' not in svcs else 'FAIL')"
```

✅ **PASS:** Prints `PASS` — analytics is removed from the registry after disabling.

```bash
# Re-enable
curl -s -X POST http://localhost/admin/api/profiles/analytics/enable \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool
sleep 20
curl -s http://localhost/admin/api/services \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -c \
  "import sys,json; svcs=[s['name'] for s in json.load(sys.stdin)]; print('PASS' if 'analytics' in svcs else 'FAIL')"
```

✅ **PASS:** Prints `PASS` — analytics service reappears as `online`.

***

**Step 5.4 — Cross-service audit log sweep**

After completing Scenarios 1–4, run an audit log check:

```bash
curl -s "http://localhost/admin/api/audit?limit=50" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool
```

✅ **PASS:** The last 50 audit entries include at minimum one entry each for:

- `action: patient.synced` from `integration-hub`
- `action: laborder.routed` from `integration-hub`
- `action: dicom.stored` from `integration-hub`
- `action: profile.changed` from `admin`
- All entries have non-null `actor`, `resource_id`, `ts`, and `outcome` fields

***

## SCENARIO 6 — Resilience \& Recovery

**Purpose:** Verify the system recovers gracefully from service restarts without data loss.

### Steps

**Step 6.1 — Integration Hub restart during active sync**

```bash
# Register a second test patient in OpenMRS (repeat Step 1.1 with different data)
# Immediately kill the integration-hub container
docker compose stop integration-hub

# Wait 5 seconds, then restart
sleep 5
docker compose start integration-hub

# Wait for recovery
sleep 30

# Verify the second patient was still synced to MPI
curl -s "http://localhost/mpi/api/patients?national_id=FR-TEST-00002" | python -m json.tool
```

✅ **PASS:** The second patient's MPI record exists — the Redis Stream retained the event and the hub replayed it on restart.
❌ **FAIL:** Patient not synced — the in-memory dedup set was cleared on restart OR Redis lost the stream event (AOF not enabled). See OBJ 3.3 in the to-do list.

***

**Step 6.2 — Redis restart (stream durability)**

```bash
# Check AOF is enabled
docker exec openhis-redis redis-cli CONFIG GET appendonly
```

✅ **PASS:** Output is `appendonly yes`.
❌ **FAIL (known gap):** Output is `appendonly no` — events are not durable across Redis restarts. See OBJ 3.3.

```bash
# Produce a test event, restart Redis, verify event survived
docker exec openhis-redis redis-cli XADD openhis.events '*' type test.event data test_payload
docker compose restart redis
sleep 10
docker exec openhis-redis redis-cli XLEN openhis.events
```

✅ **PASS:** Stream length is > 0 after restart (event survived).
❌ **FAIL:** Length is 0 — AOF persistence is not active.

***

**Step 6.3 — MPI unavailable — hub queues gracefully**

```bash
docker compose stop mpi
sleep 5

# Try to register a patient in OpenMRS
# (the hub should queue the sync attempt)

docker compose start mpi
sleep 30

# Verify the patient eventually appears
curl -s "http://localhost/mpi/api/patients?national_id=FR-TEST-00003"
```

✅ **PASS:** Patient synced after MPI came back online (retry mechanism worked).
❌ **FAIL:** Patient never synced — the `withretry` decorator may not be covering MPI calls.

***

## SCENARIO 7 — HL7 MLLP Interoperability

**Purpose:** Verify the HL7 listener accepts inbound messages and routes them into the FHIR/event spine.

### Prerequisites

```bash
# Install hl7 tools if available, or use netcat
pip install hl7
```


### Steps

**Step 7.1 — Send an inbound ADT^A01 (admit patient)**

```bash
# Construct a minimal HL7 ADT^A01 message and send via MLLP
python3 << 'EOF'
import socket, datetime

MLLP_START = b'\x0b'
MLLP_END   = b'\x1c\x0d'

msg = (
    "MSH|^~\\&|TEST_SYSTEM|TEST_FACILITY|OPENHIS|OPENHIS|"
    + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    + "||ADT^A01|MSG00001|P|2.5\r"
    "EVN|A01|" + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + "\r"
    "PID|1||FR-TEST-HL7-001^^^TEST&2.16.840.1.113883.19.5&ISO||"
    "Dupont^Marie^^^^^L||19850310|F|||15 Rue Lafayette^^Paris^^75009^FRA\r"
    "PV1|1|I|WARD^101^A^GEN||||||||||||||||V001\r"
)

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(('localhost', 2575))
sock.sendall(MLLP_START + msg.encode('utf-8') + MLLP_END)
response = sock.recv(1024)
sock.close()
print("ACK received:", response.decode('utf-8', errors='replace'))
EOF
```

✅ **PASS:** ACK message received containing `MSA|AA` (Application Accept).
❌ **FAIL:** `MSA|AE` (Application Error) or connection refused.

***

**Step 7.2 — Verify ADT patient appears in event stream**

```bash
docker exec openhis-redis redis-cli XRANGE openhis.events - + COUNT 10 | grep patient
```

✅ **PASS:** A `patient.synced` or `patient.admitted` event is present referencing `FR-TEST-HL7-001`.

***

**Step 7.3 — Send an inbound ORU^R01 (lab result)**

```bash
python3 << 'EOF'
import socket, datetime

MLLP_START = b'\x0b'
MLLP_END   = b'\x1c\x0d'

msg = (
    "MSH|^~\\&|LIS_SYSTEM|LAB|OPENHIS|OPENHIS|"
    + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    + "||ORU^R01|MSG00002|P|2.5\r"
    "PID|1||FR-TEST-HL7-001^^^TEST&2.16.840.1.113883.19.5&ISO\r"
    "OBR|1|ORD-001|FIL-001|58410-2^CBC\r"
    "OBX|1|NM|6690-2^WBC^LN||9.8|10*3/uL|4.5-11.0|N|||F\r"
    "OBX|2|NM|718-7^Hemoglobin^LN||13.5|g/dL|12.0-16.0|N|||F\r"
)

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(('localhost', 2575))
sock.sendall(MLLP_START + msg.encode('utf-8') + MLLP_END)
response = sock.recv(1024)
sock.close()
print("ACK:", response.decode('utf-8', errors='replace'))
EOF
```

✅ **PASS:** `MSA|AA` ACK received.

***

**Step 7.4 — Verify FHIR Observation created from ORU**

```bash
curl -s "http://localhost/hub/fhir/Observation?patient.identifier=FR-TEST-HL7-001" \
  -H "Accept: application/fhir+json" | python -m json.tool
```

✅ **PASS:** Bundle contains 2 `Observation` resources (WBC and Hemoglobin) with `status: final`.

***

## Consolidated Pass/Fail Summary

After completing all scenarios, fill in this matrix:


| Scenario | Description | Status | Notes |
| :-- | :-- | :-- | :-- |
| 1.1–1.5 | Patient registration \& MPI sync | ⬜ |  |
| 2.1–2.6 | Lab order round-trip | ⬜ |  |
| 3.1–3.7 | DICOM imaging workflow | ⬜ |  |
| 4.1–4.6 | SSO \& RBAC enforcement | ⬜ |  |
| 5.1–5.4 | Admin plane \& audit log | ⬜ |  |
| 6.1–6.3 | Resilience \& recovery | ⬜ |  |
| 7.1–7.4 | HL7 MLLP interoperability | ⬜ |  |

**Minimum pass threshold for a release candidate:** All Scenarios 1–5 and 7 must fully pass. Scenario 6.1 must pass. Scenarios 6.2 and 6.3 are tracked but non-blocking until OBJ 3.3 is resolved.

***

## Known Expected Failures (Current State)

Based on the codebase analysis, the following will fail until the corresponding to-do items are resolved:


| Test | Expected Failure | Blocking To-Do |
| :-- | :-- | :-- |
| 1.4 — `openelis_id` in MPI crossref | `null` — write-back not implemented | OBJ 4.4 |
| 4.3 — 403 for no-role user | May return 200 (fail-open) | OBJ 1.2 |
| 6.2 — Redis AOF durability | `appendonly no` | OBJ 3.3 |
| 6.3 — MPI-down queuing | May not retry | OBJ 3.1 |
| 3.6 — AI pipeline trigger | Consumer not wired | OBJ 3.1 |

<span style="display:none">[^1][^10][^11][^12][^13][^14][^15][^2][^3][^4][^5][^6][^7][^8][^9]</span>

<div align="center">⁂</div>

[^1]: https://discourse.ohie.org/t/ohie-testing-framework-action-from-community-leads/3123

[^2]: https://openhie.github.io/instant/docs/more-info/architecture/

[^3]: https://www.youtube.com/watch?v=KurYmn_fSE0

[^4]: https://webtech.fr/en/blog/end-to-end-testing/

[^5]: https://guides.ohie.org/getting-started/pathway-1-component-and-data-exchange/phase-2-requirements-and-design/testing

[^6]: https://medic.org/stories/developing-interoperable-scalable-and-sustainable-health-information-systems-with-openhie-and-fhir/

[^7]: https://www.youtube.com/watch?v=arPxzixIl38

[^8]: https://www.ranorex.com/blog/end-to-end-testing/

[^9]: https://docs.openfn.org/documentation/legacy/standards/openhie

[^10]: https://www.youtube.com/watch?v=vatkp1o3LrQ

[^11]: https://www.elephant-technologies.fr/blog/tests-end-to-end

[^12]: https://ohie.org/test/

[^13]: https://www.reddit.com/r/ExperiencedDevs/comments/m66kpx/what_integration_test_frameworks_do_you_use_at/

[^14]: https://www.all4test.fr/blog-du-testeur/tests-end-to-end-e2e-guide-complet/

[^15]: https://docs.openelisci.org/en/latest/deployomrs/

