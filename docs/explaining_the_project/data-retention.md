# Data Retention Policy

This document describes the data retention and archival policies for OpenHIS.
Operators must configure these settings in compliance with applicable regulations
before storing real patient data.

## Regulatory Defaults

| Jurisdiction | Regulation | Minimum retention |
|---|---|---|
| USA | HIPAA | 6 years from creation or last effective date |
| EU | GDPR + national law | Varies; typically 5–10 years for medical records |
| France | Code de la Santé Publique | 20 years for adult records |

**OpenHIS does not enforce these defaults automatically.** Operators configure
retention via environment variables.

## Configurable Retention Settings

| Env var | Default | Description |
|---|---|---|
| `AUDIT_RETENTION_DAYS` | `2190` (6 years) | Days to keep audit log rows before archival |
| `REDIS_STREAM_MAXLEN` | `100000` | Max events per Redis stream before oldest are trimmed |
| `PATIENT_DATA_RETENTION_YEARS` | `20` | Used by data export/archival tooling |

## Data Stores and Their Retention Behaviour

### PostgreSQL (MPI, Admin, RIS, Analytics)

- No automatic row deletion. Operators must implement scheduled archival jobs.
- Recommended: Use `pg_partman` for time-partitioned audit tables.

### Redis Streams (`openhis.events`, `openhis.audit`)

- Streams are bounded by `REDIS_STREAM_MAXLEN` using `MAXLEN ~` (approximate trim).
- For compliance, configure AOF persistence so events survive Redis restarts.
- For long-term audit storage, the Admin service archives the `openhis.audit`
  stream to JSON files on a configurable schedule.

### SQLite (legacy services only)

- Legacy services in `services/_legacy/` use SQLite for local state.
- These are not covered by the archival tooling. Do not use legacy services
  in production.

## Archival Process

The Admin service exposes an archival job endpoint (when enabled):

```
POST /api/admin/jobs/archive-audit?before_days=2190
```

This exports audit rows older than the configured threshold to
`/data/audit-archive/YYYY-MM-DD.jsonl` and deletes them from Postgres.

## Right to Erasure (GDPR Article 17)

OpenHIS does not implement automated right-to-erasure workflows.
Operators requiring GDPR compliance must:

1. Identify all data stores containing the patient's identifiers
2. Delete or anonymise records in: MPI, OpenMRS, OpenELIS, Odoo, Orthanc
3. Remove the patient's cross-reference entries from the MPI
4. Document the erasure action in the audit log

A future `opm gdpr-erase --patient-id <uuid>` command is planned.
