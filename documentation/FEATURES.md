# OpenHIS — Feature Inventory & Fulfilled Requirements

> Analysis date: 2026-03-25
> Codebase: /home/reda/dz_pacs

---

## EHR Service (`services/ehr`)

### Patient Management
- Create, search, update patients with full demographics (MRN, name, DOB, sex, phone, insurance)
- Per-patient allergies (substance, reaction, severity)
- Per-patient ICD-10 diagnoses

### Clinical Encounters
- Admit patients to a specific ward/bed; bed auto-marked occupied on admit
- Discharge updates encounter status and bed status (→ housekeeping)
- Encounter history per patient

### Clinical Orders
- Create LAB, IMAGING, PHARMACY, REFERRAL orders with priority (STAT / ROUTINE)
- Order lifecycle: PENDING → SENT → IN_PROGRESS → COMPLETED
- On creation: CDSS evaluation runs locally and FHIR bridge is notified

### CDSS — Clinical Decision Support
- Local rule engine evaluates orders and incoming lab results
- Generates severity-graded alerts (warning / critical) per patient
- Alerts are acknowledgeable by clinicians
- Patient list shows unacknowledged critical alert count banner

### Bed Management
- Bed registry per ward with type (standard / ICU / isolation)
- Status lifecycle: available → occupied → housekeeping → maintenance → available
- Real-time board grouped by ward with per-ward occupancy stats

### Clinical Notes
- Types: progress note, SOAP, nursing note, discharge summary, consultation
- Workflow: Draft → Sign (final, immutable) → Amend (creates new draft copy)
- File attachments on notes (upload / download / delete, max 20 MB)

### Scheduling
- Outpatient appointment booking with provider, department, date/duration
- Statuses: scheduled / completed / cancelled / no-show

### Billing
- CPT-coded billing entries per encounter
- Statuses: pending / approved / denied / paid

### Web UI
- Single-page app: patient roster, 9-tab patient chart, bed board, CDSS dashboard

### Data Model
| Table | Key Fields |
|---|---|
| patients | id, mrn, first_name, last_name, birth_date, sex, phone, insurance_id |
| encounters | id, patient_id, ward, bed, admit_date, discharge_date, status |
| clinical_orders | id, patient_id, encounter_id, order_type, priority, status, order_detail (JSON) |
| cdss_alerts | id, patient_id, order_id, severity, message, acknowledged |
| allergies | id, patient_id, substance, reaction, severity |
| diagnoses | id, patient_id, encounter_id, icd_code, description |
| appointments | id, patient_id, provider, department, date, duration, status |
| billing_records | id, encounter_id, cpt_code, description, amount, status |
| beds | id, ward, room, bed_label, bed_type, status, notes |
| clinical_notes | id, patient_id, encounter_id, note_type, status, content, signed_by, signed_at |
| note_documents | id, note_id, patient_id, filename, content_type, file_data |

---

## RIS Service (`services/ris`)

### Patient Registry
- Independent registry cross-referenced by MRN; upsert from EHR via FHIR bridge

### Imaging Orders & Worklist
- Create imaging orders with modality (CR, DX, US, CT, MR, NM), body part, priority
- Worklist sorted STAT before ROUTINE, PENDING before IN_PROGRESS
- Auto-generated accession numbers (ACC-YYYYMMDD-NNNN)

### Radiology Reports
- Workflow: DRAFT → PRELIMINARY → FINAL → ADDENDUM
- Fields: technique, findings, impression, recommendation
- Finalizing a report transitions the linked order to COMPLETED

### Web UI
- Three tabs: Worklist, Patients, Reports

### Data Model
| Table | Key Fields |
|---|---|
| patients | id, mrn, patient_name, birth_date, sex, orthanc_id |
| orders | id, patient_id, accession_number, modality, body_part, priority, status, scheduled_date |
| reports | id, order_id, technique, findings, impression, recommendation, status |

---

## LIS Service (`services/lis`)

### Specimen Management
- Accession specimens with type (blood, urine, CSF, tissue, swab, stool, sputum)
- Full custody chain: audit log of who received/processed, where, when

### Lab Orders & Test Catalog
- Test catalog: CBC, BMP, CMP, LFT, LIPID, HBA1C, COAG, UA, MICRO, TSH, and more
- Orders linked to specimens, assignable to named instruments

### Instrument Simulation
- Instruments: HEMA-01, CHEM-01, IMMUNO-01, MICRO-01, COAG-01
- Async instrument runs auto-generate realistic analyte results
- Results carry reference ranges and flags (H / L / HH / LL / normal)

### Quality Control
- QC records per instrument/test
- Westgard multi-rule evaluation: 1-2s, 1-3s, 2-2s, R-4s, 4-1s, 10-x rules

### Results Workflow
- Submit preliminary or final results in batch
- Validate (finalize) individual results
- Finalized results trigger FHIR bridge `lab-result-final` event → EHR CDSS

### Data Model
| Table | Key Fields |
|---|---|
| lab_patients | id, mrn, ehr_patient_id, name |
| specimens | id, patient_id, accession_number, specimen_type, collection_date, status, custody_log (JSON) |
| lab_orders | id, specimen_id, ehr_order_id, test_code, status, instrument_id |
| lab_results | id, order_id, analyte_code, value, unit, reference_range, flag, status, validated_by |
| qc_records | id, instrument_id, test_code, level, mean, sd, rules_violated (JSON) |
| instrument_runs | id, instrument_id, start_time, end_time, orders_processed |

---

## Admin Service (`services/admin`)

### Authentication & Sessions
- Username/password login with PBKDF2-SHA256 hashed passwords
- Bearer token sessions with configurable TTL (SESSION_TTL_HOURS env var)
- Logout immediately invalidates token
- Cannot delete own account (403)

### User Management
- Roles: admin, superadmin
- Create / delete users (superadmin only)
- Password change with minimum length validation (6 characters)
- Duplicate username prevention (409)

### System Configuration
- Key-value store for system-wide settings: maintenance_mode, hl7_mllp_enabled,
  ai_auto_trigger_enabled, session_timeout_hours, radiology_sla_hours, critical_alert_email
- All changes recorded in audit log

### Service Health Monitor
- Concurrent polling of all microservices: EHR, RIS, LIS, HL7, FHIR bridge, MPI,
  Portal, Analytics, Orthanc, AI Controller

### Audit Log
- Events: login, logout, user-created, password-changed, config-changed
- Each entry records actor, target, timestamp, details

### Announcements
- System-wide announcements with severity: info / warning / critical / success
- Active/inactive toggle

### Data Model
| Table | Key Fields |
|---|---|
| admin_users | id, username, password_hash, role, created_at, last_login |
| admin_sessions | id, user_id, token, expires_at |
| audit_log | id, actor, action, target, details, created_at |
| system_config | key, value, updated_at, updated_by |
| announcements | id, title, body, severity, active, created_at |

---

## FHIR Bridge (`services/fhir-bridge`)

Stateless event router — no database.

### Event Routing
| Inbound Event | Action |
|---|---|
| `patient-created` | Sync to RIS `/patients/from-ehr` and LIS `/lab-patients` |
| `imaging-order` | Create RIS order; PATCH accession number back to EHR |
| `lab-order` | Create LIS specimen + lab order; PATCH LIS reference back to EHR |
| `pharmacy-order` | Route to pharmacy service |
| `note-finalized` | Push FHIR Composition to FHIR server |
| `lab-result-final` | POST EHR `/orders/from-lis-result` to trigger CDSS |
| `report-final` | POST EHR CDSS endpoint |

### FHIR Translation
- Patient → FHIR R4 Patient
- Order → FHIR R4 ServiceRequest
- Lab result → FHIR R4 DiagnosticReport
- Clinical note → FHIR R4 Composition
- Prescription → FHIR R4 MedicationRequest
- Imaging study → FHIR R4 ImagingStudy
- AI result → FHIR R4 Observation

---

## HL7 v2 Gateway (`services/hl7`)

### MLLP TCP Server
- Port 2575, asyncio-based, concurrent connections
- MLLP framing: start-of-block 0x0B / end-of-block 0x1C / carriage return 0x0D
- Returns AA (accepted) or AE (error) ACK
- AE returned for messages with no MSH segment

### Message Handling
| Message Type | Action |
|---|---|
| ADT^A01 | Admit → create/find patient in EHR, create encounter |
| ADT^A02 | Transfer → update encounter ward/bed |
| ADT^A03 | Discharge → update encounter status |
| ADT^A04 | Register outpatient → sync patient to MPI |
| ADT^A08 | Update patient demographics |
| ADT^A40 | Merge patients via MPI |
| ORU^R01 | Lab result → forward to EHR CDSS |

### Message Log
- Persists all inbound and outbound messages with raw HL7, parsed fields, status, errors
- REST endpoint for inbound submission without MLLP
- REST endpoints to build and send outbound ADT / ORU messages

### Data Model
| Table | Key Fields |
|---|---|
| messages | id, direction, msg_type, control_id, sending_app, patient_id, patient_name, raw, status, error_msg |

---

## DICOM Simulator (`services/simulator`)

### Modality Generation
| Modality | Details |
|---|---|
| CR | Computed Radiography — single frame, 12-bit |
| DX | Digital X-ray — single frame, 12-bit |
| US | Ultrasound — single frame, 8-bit |
| CT | Multi-slice (configurable count), signed 16-bit, RescaleSlope/Intercept |
| MR | Multi-slice, sequences: SE/GRE/T2/FLAIR/EPI/STIR/SPGR |
| NM | Nuclear medicine |

- Body-part-aware pixel generation (anatomically plausible patterns per body part)
- Full DICOM tag set: Patient, Study, Series, SOP, pixel data, window/level

### Orthanc Integration
- Uploads generated instances directly to Orthanc PACS
- Job history (last 50 jobs) with Orthanc instance IDs, size, timestamp
- Returns 502 if Orthanc rejects the upload

---

## Cross-Cutting Requirements

| Requirement | How Fulfilled |
|---|---|
| Microservice isolation | 7 independent FastAPI services, each with own SQLite DB |
| Event-driven integration | FHIR bridge as central event bus |
| FHIR R4 compliance | 7 resource translators (Patient, ServiceRequest, DiagnosticReport, Composition, MedicationRequest, ImagingStudy, Observation) |
| HL7 v2 interoperability | Full MLLP TCP server + ADT/ORU parser and builder |
| DICOM image storage | Orthanc PACS integration + multi-modality simulator |
| Clinical decision support | Local rule engine with severity-graded acknowledgeable alerts |
| Role-based access control | Admin service: admin / superadmin roles with endpoint-level enforcement |
| Audit trail | All admin actions logged with actor, action, target, timestamp |
| Laboratory quality control | Westgard multi-rule evaluation per instrument/test |
| Chain of custody | Specimen custody JSON log in LIS |
| Containerisation-ready | Service URLs use Docker DNS names (ehr:8003, ris:8002, etc.) |

---

## Service Port Map

| Service | Port |
|---|---|
| RIS | 8002 |
| EHR | 8003 |
| LIS | 8004 |
| FHIR Bridge | 8005 |
| HL7 Gateway | 8006 (HTTP) / 2575 (MLLP) |
| Admin | 8000 (assumed) |
| Simulator | varies |
| Orthanc PACS | 8042 |
| FHIR Server (HAPI) | 8080 |
