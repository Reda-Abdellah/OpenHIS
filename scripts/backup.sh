#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# OpenHIS backup tool — databases + named volumes for the active profiles.
#
# Usage:
#   bash scripts/backup.sh [--dry-run] [--cold] [--all-profiles]
#                          [--skip-rebuildable] [--keycloak-export]
#
# Hot mode (default — stack stays up):
#   * logical DB dumps: pg_dumpall/pg_dump (shared postgres: orthanc, mpi,
#     ehr, hapi_fhir), pg_dump clinlims (openelis-db), pg_dump odoo (odoo-db,
#     skipped with a warning while the DB hasn't been created yet),
#     mysqldump openmrs (openmrs-db)
#   * redis: BGSAVE checkpoint, then a tar of the redis-data volume
#     (dump.rdb + appendonlydir — AOF is on, see infra/redis/redis.conf)
#   * SQLite volumes: consistent copy via `sqlite3 .backup` in a throwaway
#     alpine container, falling back to a plain tar with a warning
#   * file volumes: tar via a throwaway alpine container
# Cold mode (--cold — takes the stack DOWN first, leaves it down):
#   * raw tars of every named volume, including the DB data volumes
#     (pg-data, openelis-pg, odoo-pg, openmrs-mysql)
#
# Flags:
#   --dry-run           print the exact command plan, execute nothing
#   --all-profiles      cover every compose/profiles/*.yml (ignore env)
#   --skip-rebuildable  skip openelis-lucene (index is rebuilt by OpenELIS)
#   --keycloak-export   also export the Keycloak realm (kc.sh export)
#
# Output: $BACKUP_DIR/<UTC-timestamp>/ with manifest.json (git SHA, sizes,
# sha256) + SHA256SUMS. Restore with scripts/restore.sh — see scripts/README.md.
#
# Profiles: respects $OPENHIS_PROFILES (environment, then .env), same as the
# Makefile. Compose project is pinned to `openhis` and every container call
# goes through `docker compose -p openhis exec -T <service>`, so container
# name drift cannot break the scripts. Credentials are resolved INSIDE the
# containers (POSTGRES_USER, MYSQL_ROOT_PASSWORD, REDIS_PASSWORD, … are set
# by compose from .env) — nothing is hardcoded here. compose/overrides/ci.yml
# is never sourced (it swaps named volumes for tmpfs).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

usage() {
    grep '^#' "$0" | sed -n '2,36p' | sed 's/^# \{0,1\}//'
}

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=0
COLD=0
ALL_PROFILES=0
SKIP_REBUILDABLE=0
KEYCLOAK_EXPORT=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)          DRY_RUN=1 ;;
        --cold)             COLD=1 ;;
        --all-profiles)     ALL_PROFILES=1 ;;
        --skip-rebuildable) SKIP_REBUILDABLE=1 ;;
        --keycloak-export)  KEYCLOAK_EXPORT=1 ;;
        -h|--help)          usage; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# ── Profile resolution (mirrors the Makefile) ────────────────────────────────
if (( ALL_PROFILES )); then
    PROFILES=""
    for _f in compose/profiles/*.yml; do
        _p="$(basename "$_f" .yml)"
        PROFILES="${PROFILES:+${PROFILES},}${_p}"
    done
else
    PROFILES="${OPENHIS_PROFILES:-}"
    if [[ -z "$PROFILES" && -f .env ]]; then
        PROFILES="$(grep -s '^OPENHIS_PROFILES=' .env | cut -d'=' -f2- || true)"
    fi
    PROFILES="${PROFILES:-emr,laboratory,erp,imaging,analytics}"
fi

COMPOSE=(docker compose -p openhis -f compose/base.yml)
IFS=',' read -r -a _profile_list <<< "$PROFILES"
for _p in "${_profile_list[@]}"; do
    [[ -z "$_p" ]] && continue
    if [[ ! -f "compose/profiles/${_p}.yml" ]]; then
        echo "ERROR: unknown profile '${_p}' (no compose/profiles/${_p}.yml)" >&2
        exit 2
    fi
    COMPOSE+=(-f "compose/profiles/${_p}.yml")
done

profile_active() {
    [[ ",${PROFILES}," == *",$1,"* ]]
}

# ── Output location ───────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
case "$BACKUP_DIR" in
    /*) OUT_ABS="${BACKUP_DIR%/}/${TS}" ;;
    *)  OUT_ABS="${REPO_ROOT}/${BACKUP_DIR#./}/${TS}" ;;
esac
MODE=hot; (( COLD )) && MODE=cold

# Databases hosted on the shared postgres service (kept in sync with
# compose/base.yml POSTGRES_DB + infra/postgres/init-databases.sh — the
# offline unit test cross-checks this list against those files).
CORE_PG_DATABASES=(orthanc mpi ehr hapi_fhir)

# ── Execution wrappers (--dry-run prints the exact command, runs nothing) ────
note() { printf '# %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

run() {
    if (( DRY_RUN )); then
        printf '+ %s\n' "$(printf '%q ' "$@")"
        return 0
    fi
    "$@"
}

run_to() {  # run_to <outfile> <cmd...>   — cmd's stdout goes to <outfile>
    local out="$1"; shift
    if (( DRY_RUN )); then
        printf '+ %s> %q\n' "$(printf '%q ' "$@")" "$out"
        return 0
    fi
    "$@" > "$out"
}

service_running() {
    if (( DRY_RUN )); then
        return 0  # plan the full backup; real runs skip stopped services
    fi
    [[ -n "$("${COMPOSE[@]}" ps -q --status running "$1" 2>/dev/null)" ]]
}

# ── Building blocks ───────────────────────────────────────────────────────────
backup_volume() {  # backup_volume <volume> <subdir: volumes|cold>
    local vol="$1" sub="$2"
    run docker run --rm \
        -v "openhis_${vol}:/src:ro" \
        -v "${OUT_ABS}/${sub}:/dst" \
        alpine tar czf "/dst/${vol}.tgz" -C /src .
}

backup_sqlite() {  # backup_sqlite <volume> <dbfile>  — consistent .backup copy
    local vol="$1" db="$2"
    run docker run --rm \
        -v "openhis_${vol}:/data" \
        -v "${OUT_ABS}/sqlite:/dst" \
        -v "${OUT_ABS}/volumes:/fallback" \
        alpine sh -c "[ -f '/data/${db}' ] || { echo 'note: /data/${db} not present — skipping'; exit 0; }; \
if apk add --no-cache sqlite >/dev/null 2>&1; then \
sqlite3 '/data/${db}' '.backup /dst/${vol}__${db}'; \
else echo 'WARN: sqlite unavailable — falling back to a plain tar of ${vol}' >&2; \
tar czf '/fallback/${vol}.tgz' -C /data .; fi"
}

backup_postgres_core() {
    note "volume pg-data covered by logical pg_dump below (use --cold for a raw tar)"
    if ! service_running postgres; then
        warn "postgres is not running — skipping shared-postgres dumps"
        return 0
    fi
    # shellcheck disable=SC2016  # $POSTGRES_USER expands inside the container
    run_to "${OUT_ABS}/db/postgres/globals.sql" \
        "${COMPOSE[@]}" exec -T postgres sh -c 'exec pg_dumpall --globals-only -U "$POSTGRES_USER"'
    local db
    for db in "${CORE_PG_DATABASES[@]}"; do
        run_to "${OUT_ABS}/db/postgres/${db}.dump" \
            "${COMPOSE[@]}" exec -T postgres sh -c "exec pg_dump -Fc -U \"\$POSTGRES_USER\" ${db}"
    done
}

backup_openelis_db() {
    note "volume openelis-pg covered by logical pg_dump below (use --cold for a raw tar)"
    if ! service_running openelis-db; then
        warn "openelis-db is not running — skipping clinlims dump"
        return 0
    fi
    # shellcheck disable=SC2016  # $POSTGRES_USER (=postgres) expands in-container
    run_to "${OUT_ABS}/db/openelis/clinlims.dump" \
        "${COMPOSE[@]}" exec -T openelis-db sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" clinlims'
}

backup_odoo_db() {
    note "volume odoo-pg covered by logical pg_dump below (use --cold for a raw tar)"
    if ! service_running odoo-db; then
        warn "odoo-db is not running — skipping odoo dump"
        return 0
    fi
    local odoo_db="${ODOO_DB:-}"
    if [[ -z "$odoo_db" && -f .env ]]; then
        odoo_db="$(grep -s '^ODOO_DB=' .env | cut -d'=' -f2- || true)"
    fi
    odoo_db="${odoo_db:-odoo}"
    local check=("${COMPOSE[@]}" exec -T odoo-db sh -c \
        "exec psql -U \"\$POSTGRES_USER\" -tAc \"SELECT 1 FROM pg_database WHERE datname='${odoo_db}'\"")
    if (( DRY_RUN )); then
        run "${check[@]}"
    else
        local exists
        exists="$("${check[@]}" 2>/dev/null || true)"
        if [[ "$exists" != *1* ]]; then
            warn "odoo database '${odoo_db}' does not exist yet (it is created lazily" \
                 "via the Odoo UI / odoo-init) — skipping dump"
            return 0
        fi
    fi
    run_to "${OUT_ABS}/db/odoo/${odoo_db}.dump" \
        "${COMPOSE[@]}" exec -T odoo-db sh -c "exec pg_dump -Fc -U \"\$POSTGRES_USER\" ${odoo_db}"
}

backup_openmrs_db() {
    note "volume openmrs-mysql covered by logical mysqldump below (use --cold for a raw tar)"
    if ! service_running openmrs-db; then
        warn "openmrs-db is not running — skipping openmrs dump"
        return 0
    fi
    # shellcheck disable=SC2016  # $MYSQL_ROOT_PASSWORD expands inside the container
    run_to "${OUT_ABS}/db/openmrs/openmrs.sql" \
        "${COMPOSE[@]}" exec -T openmrs-db sh -c \
        'exec mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers openmrs'
}

backup_redis() {
    if service_running redis; then
        # shellcheck disable=SC2016  # $REDIS_PASSWORD expands inside the container
        run "${COMPOSE[@]}" exec -T redis sh -c \
            'exec redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD" --no-auth-warning} BGSAVE'
        if (( DRY_RUN )); then
            note "poll 'redis-cli INFO persistence' until rdb_bgsave_in_progress:0 (timeout 120s)"
        else
            local i
            for i in $(seq 1 60); do
                # shellcheck disable=SC2016
                if "${COMPOSE[@]}" exec -T redis sh -c \
                    'exec redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD" --no-auth-warning} INFO persistence' \
                    | grep -q 'rdb_bgsave_in_progress:0'; then
                    break
                fi
                if (( i == 60 )); then
                    warn "BGSAVE still in progress after 120s — tarring redis-data anyway"
                fi
                sleep 2
            done
        fi
    else
        warn "redis is not running — tarring redis-data without a BGSAVE checkpoint"
    fi
    # Captures dump.rdb + appendonlydir (AOF enabled in infra/redis/redis.conf).
    backup_volume redis-data volumes
}

backup_keycloak() {
    if ! service_running keycloak; then
        warn "keycloak is not running — skipping realm export"
        return 0
    fi
    run "${COMPOSE[@]}" exec -T keycloak \
        /opt/keycloak/bin/kc.sh export --dir /tmp/keycloak-export --realm openhis
    run mkdir -p "${OUT_ABS}/keycloak"
    if (( DRY_RUN )); then
        note "docker cp \$(docker compose -p openhis ps -q keycloak):/tmp/keycloak-export/. ${OUT_ABS}/keycloak/"
    else
        local cid
        cid="$("${COMPOSE[@]}" ps -q keycloak)"
        docker cp "${cid}:/tmp/keycloak-export/." "${OUT_ABS}/keycloak/"
    fi
}

write_manifest() {
    if (( DRY_RUN )); then
        note "write ${OUT_ABS}/SHA256SUMS + ${OUT_ABS}/manifest.json (git SHA, per-artifact size + sha256)"
        return 0
    fi
    local git_sha
    git_sha="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    (
        cd "$OUT_ABS"
        # shellcheck disable=SC2094  # SHA256SUMS is excluded from the find by name
        find . -type f ! -name SHA256SUMS ! -name manifest.json \
            | sed 's|^\./||' | LC_ALL=C sort \
            | while IFS= read -r f; do sha256sum "$f"; done > SHA256SUMS
    )
    {
        echo '{'
        echo "  \"backup_format\": 1,"
        echo "  \"created_utc\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
        echo "  \"git_sha\": \"${git_sha}\","
        echo "  \"mode\": \"${MODE}\","
        echo "  \"profiles\": \"${PROFILES}\","
        echo '  "artifacts": ['
        local first=1 line sum path size
        while IFS= read -r line; do
            sum="${line%% *}"
            path="${line#*  }"
            size="$(stat -c %s "${OUT_ABS}/${path}")"
            (( first )) || printf ',\n'
            printf '    {"path": "%s", "bytes": %s, "sha256": "%s"}' "$path" "$size" "$sum"
            first=0
        done < "${OUT_ABS}/SHA256SUMS"
        printf '\n  ]\n}\n'
    } > "${OUT_ABS}/manifest.json"
}

# ── Plan ──────────────────────────────────────────────────────────────────────
echo "OpenHIS backup → ${OUT_ABS}  (mode: ${MODE}, profiles: ${PROFILES})"
(( DRY_RUN )) && note "DRY RUN — printing the command plan, executing nothing"

run mkdir -p "${OUT_ABS}/volumes" "${OUT_ABS}/sqlite" \
    "${OUT_ABS}/db/postgres" "${OUT_ABS}/db/openelis" \
    "${OUT_ABS}/db/odoo" "${OUT_ABS}/db/openmrs"

if (( COLD )); then
    run mkdir -p "${OUT_ABS}/cold"
    note "cold mode: stopping the whole stack before the raw volume snapshot"
    run "${COMPOSE[@]}" down
fi

# ── base (always on): shared postgres, redis, SQLite service volumes ─────────
if (( COLD )); then
    backup_volume pg-data cold
    backup_volume redis-data volumes
    backup_volume admin-data volumes
    backup_volume hl7-data volumes
    backup_volume hub-audit volumes
else
    backup_postgres_core
    backup_redis
    backup_sqlite admin-data admin.db
    backup_sqlite hl7-data hl7.db
    backup_sqlite hub-audit hub-audit.db
fi

# ── emr: OpenMRS (MySQL + app data) ───────────────────────────────────────────
if profile_active emr; then
    if (( COLD )); then
        backup_volume openmrs-mysql cold
    else
        backup_openmrs_db
    fi
    backup_volume openmrs-data volumes
fi

# ── laboratory: OpenELIS (postgres), Mirth (Derby), Lucene index ─────────────
if profile_active laboratory; then
    if (( COLD )); then
        backup_volume openelis-pg cold
    else
        backup_openelis_db
    fi
    if (( SKIP_REBUILDABLE )); then
        note "openelis-lucene skipped (--skip-rebuildable: OpenELIS rebuilds the index)"
    else
        backup_volume openelis-lucene volumes
    fi
fi

# ── erp: Odoo (postgres + filestore) ─────────────────────────────────────────
if profile_active erp; then
    if (( COLD )); then
        backup_volume odoo-pg cold
    else
        backup_odoo_db
    fi
    backup_volume odoo-data volumes
fi

# ── imaging: Orthanc (pixels; index lives in shared postgres), RIS, AI ───────
if profile_active imaging; then
    note "orthanc-data (pixel data) and the orthanc pg_dump (index) are taken in" \
         "the same backup window — always restore them together"
    backup_volume orthanc-data volumes
    if (( COLD )); then
        backup_volume ris-data volumes
        backup_volume ai-controller-db volumes
    else
        backup_sqlite ris-data ris.db
        backup_sqlite ai-controller-db ai-controller.db
    fi
    backup_volume ai-jobs volumes
fi

# ── analytics: metrics + portal sessions (SQLite) ─────────────────────────────
if profile_active analytics; then
    if (( COLD )); then
        backup_volume analytics-data volumes
        backup_volume portal-sessions volumes
    else
        backup_sqlite analytics-data analytics.db
        backup_sqlite portal-sessions portal.db
    fi
fi

# ── optional: Keycloak realm export (no volume — otherwise unrecoverable) ────
if (( KEYCLOAK_EXPORT )); then
    backup_keycloak
fi

write_manifest

if (( COLD )); then
    note "cold backup complete — the stack was left DOWN; restart it with: make up"
fi
echo "Backup finished: ${OUT_ABS}"
