# ADR-0003: MPI as the Identity Spine

**Status:** Accepted
**Date:** 2025-01-01

## Context

A patient may exist simultaneously in OpenMRS (with an OpenMRS UUID), OpenELIS
(with a lab accession number), and Odoo (with a partner ID). Without a shared
identity layer, there is no reliable way to join records across systems or detect
duplicates.

Options evaluated:
- **Use OpenMRS as the system of record** — OpenMRS UUIDs become the canonical
  identifier; other systems reference them. Simple, but creates a hard dependency
  on OpenMRS for all identity lookups.
- **External MPI (OpenEMPI, HAPI FHIR Patient/$match)** — standards-based but
  adds infrastructure complexity.
- **Custom lightweight MPI service** — a small service that stores a master
  patient index and cross-reference table linking system-specific IDs.

## Decision

Build a **custom `mpi` service** that:

1. Assigns a stable `master_id` (UUID) to every patient at registration.
2. Maintains a `cross_references` table mapping `(system, system_id) → master_id`.
3. Exposes a REST API (`/api/patients`, `/api/lookup`) consumed by other services.
4. Subscribes to `patient.synced` events from the integration-hub to keep
   cross-references up to date without polling.

The MRN (medical record number) is the matching key between systems.

## Consequences

- **Stable identity across systems** — any service can resolve a patient by MRN
  and get back OpenMRS UUID, OpenELIS ID, and Odoo partner ID in one call.
- **PostgreSQL-backed** — originally shipped on SQLite; migrated to PostgreSQL
  (Postgres-specific DDL: `SERIAL`, `ON CONFLICT`, `RETURNING`), which removes
  the single-replica limitation this ADR initially accepted.
- **Eventual consistency** — cross-references are updated asynchronously via the
  event bus; there is a brief window where a newly synced patient is not yet
  cross-referenced.
- **MRN as the matching key** — assumes MRN is assigned before the patient enters
  any downstream system. Tighten the patient registration workflow if this
  assumption breaks.

## Implementation

See `services/mpi/` and the MPI bus consumer
[services/mpi/bus_consumer.py](../../services/mpi/bus_consumer.py).
