# scripts/ — committed operational tooling

Most files in this directory are local helper scripts and are git-ignored
(see `.gitignore`). The committed tooling documented here is the exception.

## Backup & restore (`backup.sh` / `restore.sh`)

Profile-aware backup/restore for the whole OpenHIS stack: every database and
every named Docker volume of the active profiles. Pure bash + `docker compose
-p openhis exec` — no extra dependencies on the host.

### Taking a backup

```bash
make backup                                  # hot backup, active profiles
make backup ARGS="--cold"                    # cold backup (stack goes DOWN)
make backup ARGS="--keycloak-export"         # + Keycloak realm export
make backup ARGS="--skip-rebuildable"        # skip the OpenELIS Lucene index
bash scripts/backup.sh --dry-run             # print the plan, run nothing
```

Output lands in `$BACKUP_DIR/<UTC-timestamp>/` (default `./backups/`, which
is git-ignored) with:

| Path                | Contents                                                              |
|---------------------|-----------------------------------------------------------------------|
| `db/postgres/`      | `pg_dumpall --globals-only` + `pg_dump -Fc` of orthanc, mpi, ehr, hapi_fhir |
| `db/openelis/`      | `pg_dump -Fc` of clinlims                                             |
| `db/odoo/`          | `pg_dump -Fc` of the odoo DB (skipped while it doesn't exist yet)     |
| `db/openmrs/`       | `mysqldump --single-transaction --routines --triggers` of openmrs     |
| `sqlite/`           | `<volume>__<file>.db` — consistent `sqlite3 .backup` copies            |
| `volumes/`          | tars of file volumes (orthanc-data, redis-data, …)                      |
| `cold/`             | raw DB volume tars (`--cold` only: pg-data, openelis-pg, odoo-pg, openmrs-mysql) |
| `keycloak/`         | realm export (`--keycloak-export` only)                               |
| `manifest.json`     | git SHA, mode, profiles, per-artifact size + sha256                   |
| `SHA256SUMS`        | `sha256sum -c`-compatible checksums (verified on restore)             |

Hot mode keeps the stack running: databases are dumped logically (consistent
online dumps), redis gets a `BGSAVE` checkpoint before its volume is tarred
(AOF is enabled, so the appendonlydir is captured too), and SQLite files are
copied with `sqlite3 .backup` inside a throwaway alpine container (falling
back to a plain tar with a warning if sqlite can't be installed).

Cold mode (`--cold`) stops the stack first and tars every volume raw,
including the DB data directories — byte-exact, but with downtime. The stack
is left down; restart with `make up`.

Profiles are resolved exactly like the Makefile: `$OPENHIS_PROFILES` from the
environment, then `.env`, defaulting to the full stack. `--all-profiles`
forces coverage of every `compose/profiles/*.yml`. The CI override
(`compose/overrides/ci.yml`, tmpfs volumes) is never used. All credentials
(POSTGRES_USER, OPENMRS_DB_ROOT_PASSWORD, OPENELIS_PG_PASSWORD,
ODOO_DB_PASSWORD, REDIS_PASSWORD, …) are resolved *inside* the containers,
where compose has already injected the `.env` values — nothing is hardcoded.

Caveats worth knowing:

- **Orthanc** splits its data: the index lives in the shared postgres
  (`orthanc` DB), pixel data in the `orthanc-data` volume. `backup.sh` takes
  both in the same backup window — always restore them **together**.
- **openelis-lucene** is a rebuildable search index — `--skip-rebuildable`
  drops it from the backup; OpenELIS rebuilds it on first use.
- **Keycloak** has no volume in the base stack (realm imported from
  `infra/keycloak/`), so runtime state (new users, rotated secrets) is only
  captured with `--keycloak-export`.
- **Odoo** creates its database lazily (first UI visit / odoo-init); the dump
  is skipped with a warning until then.

### Restoring

```bash
make restore BACKUP=backups/20260612T101500Z
make restore BACKUP=backups/20260612T101500Z ARGS="--yes"      # no prompt
make restore BACKUP=backups/20260612T101500Z ARGS="--dry-run"  # preview only
```

`restore.sh` verifies every artifact against `SHA256SUMS` (and refuses to
continue on any mismatch), asks for a loud confirmation (type `RESTORE`;
`--yes` skips it), then: stops the stack, restores the volume tars (redis-data
strictly before redis restarts), copies SQLite files back, starts only the DB
containers and waits on their healthchecks, replays the logical dumps
(`pg_restore --clean --if-exists`, `mysql`, and a drop + `createdb` +
`pg_restore` for odoo), and finally brings the full stack back up. When a
`cold/` raw tar exists for a DB volume, the physical restore wins and the
logical replay for that engine is skipped.

A post-restore checklist is printed at the end (`make health`, re-run
`odoo-init` after an odoo DB restore, Keycloak realm import is manual, …).

### Self-test (offline, no Docker needed)

Both scripts support `--dry-run`, printing the exact command plan without
executing anything. `tests/unit/platform/test_backup_scripts.py` parses the
named volumes and DB containers out of `compose/base.yml` +
`compose/profiles/*.yml` and asserts the dry-run plan covers all of them —
**adding a volume or DB to compose without updating `backup.sh` fails the
unit suite**. It also shellchecks both scripts (skipped if shellcheck is not
installed) and exercises `restore.sh` against a fixture backup, including the
tampered-checksum refusal path.

```bash
pytest tests/unit/platform/test_backup_scripts.py -q
```

## Other committed scripts

- `gen_dev_certs.sh` — self-signed TLS material for local development.
