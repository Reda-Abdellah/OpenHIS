# Deployment Profiles

OpenHIS uses a profile system to let operators deploy only the modules they need.
Profiles map to Docker Compose service groups and nginx routing rules.

## How Profiles Work

1. `opm enable <profile>` writes the profile to `.env` as `OPENHIS_PROFILES`
2. OPM regenerates nginx routing config
3. OPM calls `docker compose up -d` for the services in the profile
4. The Admin UI reflects the new service topology

## Available Profiles

### `base` (always on)
- Admin service
- MPI (Master Patient Index)
- Integration Hub (FHIR/REST bus)
- HL7 MLLP bridge
- Redis
- Postgres
- Nginx reverse proxy
- Keycloak (SSO)

**Required RAM:** 2 GB

### `emr`
Adds: OpenMRS (EMR)

**Requires:** `base`  
**RAM:** +2 GB  
**Use case:** Outpatient consultations, inpatient admissions, clinical notes

### `laboratory`
Adds: OpenELIS (LIS)

**Requires:** `base`  
**RAM:** +1 GB  
**Use case:** Lab order entry, specimen tracking, result reporting

### `imaging`
Adds: Orthanc (PACS), OHIF Viewer, RIS service, AI Controller

**Requires:** `base`  
**RAM:** +3 GB  
**Use case:** DICOM storage, radiology reporting, AI-assisted reads

### `erp`
Adds: Odoo (pharmacy, billing, inventory)

**Requires:** `base`  
**RAM:** +2 GB  
**Use case:** Pharmacy dispensing, invoicing, supply chain

### `analytics`
Adds: Analytics service, Patient Portal

**Requires:** `base`  
**RAM:** +1 GB  
**Use case:** Clinical dashboards, patient self-service portal

## Profile Contract

Every profile is defined in `compose/profiles/<name>.yml` and must:

1. Declare all services as a named Docker Compose profile
2. List dependent services via `depends_on`
3. Have a corresponding entry in `platform/profileengine.py`
4. Include nginx route stubs for each exposed UI

## Combining Profiles

Profiles are additive. Enable as many as your hardware supports:

```bash
opm enable emr
opm enable laboratory
opm enable imaging
make up
```

Check current status at any time:

```bash
opm status
```
