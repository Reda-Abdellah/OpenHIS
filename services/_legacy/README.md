# Legacy Services — FROZEN

These services are **frozen reference implementations** and are not deployed by default.
They exist for local development reference and historical context only.

| Service | Replaced by |
|---|---|
| `ehr/` | OpenMRS (via `integration-hub`) |
| `lis/` | OpenELIS (via `integration-hub`) |
| `pharmacy/` | Odoo ERP (via `integration-hub`) |
| `fhir-bridge/` | `integration-hub` (built-in FHIR R4 adapter) |

**Do not extend these services.** Open a new service under `services/` instead.
To enable them locally for reference, use the `legacy` compose profile:

```bash
docker compose --profile legacy up
```
