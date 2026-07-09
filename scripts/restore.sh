#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# OpenHIS restore tool — symmetric counterpart of scripts/backup.sh.
#
# Usage:
#   bash scripts/restore.sh [--dry-run] [--yes] [--all-profiles] <backup-dir>
#
# Steps:
#   1. verify every artifact against SHA256SUMS (refuses to restore on
#      mismatch — even in --dry-run)
#   2. loud confirmation prompt (type RESTORE; --yes skips it for automation)
#   3. docker compose down (affected services = whole stack, volumes kept)
#   4. restore volume tars (volumes/ + cold/) into the named volumes via a
#      throwaway alpine container — redis-data is restored BEFORE redis starts
#   5. copy SQLite artifacts back into their volumes
#   6. start only the DB containers (up -d --wait → healthchecks), then
#      pg_restore --clean --if-exists / mysql / drop+recreate the odoo DB
#   7. restart the full stack and print the post-restore checklist
#
# When a cold/ raw tar exists for a DB volume (pg-data, openelis-pg, odoo-pg,
# openmrs-mysql) the physical restore wins and the logical restore for that
# engine is skipped.
#
# Flags:
#   --dry-run       print the exact command plan, execute nothing destructive
#   --yes           skip the confirmation prompt (automation)
#   --all-profiles  use every compose/profiles/*.yml (ignore env)
#
# Profiles: respects $OPENHIS_PROFILES (environment, then .env), same as the
# Makefile. Credentials are resolved INSIDE the containers (set by compose
# from .env) — nothing is hardcoded. compose/overrides/ci.yml is never used.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

usage() {
    grep '^#' "$0" | sed -n '2,31p' | sed 's/^# \{0,1\}//'
}

# ── Flags + positional backup dir ─────────────────────────────────────────────
DRY_RUN=0
ASSUME_YES=0
ALL_PROFILES=0
BACKUP_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)      DRY_RUN=1 ;;
        --yes)          ASSUME_YES=1 ;;
        --all-profiles) ALL_PROFILES=1 ;;
        -h|--help)      usage; exit 0 ;;
        -*) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [[ -n "$BACKUP_PATH" ]]; then
                echo "ERROR: exactly one backup directory expected" >&2; exit 2
            fi
            BACKUP_PATH="$1"
            ;;
    esac
    shift
done

if [[ -z "$BACKUP_PATH" ]]; then
    echo "ERROR: missing backup directory argument" >&2
    usage >&2
    exit 2
fi
if [[ ! -d "$BACKUP_PATH" ]]; then
    echo "ERROR: backup directory not found: ${BACKUP_PATH}" >&2
    exit 2
fi
BACKUP_ABS="$(cd "$BACKUP_PATH" && pwd)"
if [[ ! -f "${BACKUP_ABS}/manifest.json" || ! -f "${BACKUP_ABS}/SHA256SUMS" ]]; then
    echo "ERROR: ${BACKUP_ABS} is not an OpenHIS backup (manifest.json / SHA256SUMS missing)" >&2
    exit 2
fi

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

run_from() {  # run_from <infile> <cmd...>   — cmd's stdin comes from <infile>
    local infile="$1"; shift
    if (( DRY_RUN )); then
        printf '+ %s< %q\n' "$(printf '%q ' "$@")" "$infile"
        return 0
    fi
    "$@" < "$infile"
}

# Volumes are normally still present after `compose down` (no -v). On a fresh
# host, recreate them with compose's labels so `up` accepts them afterwards.
ensure_volume() {
    local vol="$1"
    if (( DRY_RUN )); then
        note "ensure volume openhis_${vol} exists (docker volume create" \
             "--label com.docker.compose.project=openhis" \
             "--label com.docker.compose.volume=${vol} openhis_${vol})"
        return 0
    fi
    docker volume inspect "openhis_${vol}" >/dev/null 2>&1 && return 0
    docker volume create \
        --label "com.docker.compose.project=openhis" \
        --label "com.docker.compose.volume=${vol}" \
        "openhis_${vol}" >/dev/null
}

cold_has() {
    [[ -f "${BACKUP_ABS}/cold/$1.tgz" ]]
}

# ── 1. Verify checksums (always executed — read-only) ────────────────────────
echo "Verifying artifact checksums against ${BACKUP_ABS}/SHA256SUMS ..."
if ! (cd "$BACKUP_ABS" && sha256sum --check --quiet SHA256SUMS); then
    echo "ERROR: artifact sha256 checksum verification FAILED — refusing to restore" >&2
    exit 1
fi
echo "Checksums OK."

# ── 2. Loud confirmation ──────────────────────────────────────────────────────
if (( ! DRY_RUN && ! ASSUME_YES )); then
    cat >&2 <<EOF

  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  !!  DESTRUCTIVE OPERATION                                           !!
  !!                                                                  !!
  !!  This will STOP the OpenHIS stack and OVERWRITE its databases    !!
  !!  and data volumes with the contents of:                          !!
  !!      ${BACKUP_ABS}
  !!                                                                  !!
  !!  Everything written since that backup will be LOST.              !!
  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

EOF
    read -r -p "Type RESTORE to continue: " _reply
    if [[ "$_reply" != "RESTORE" ]]; then
        echo "Aborted — nothing was changed."
        exit 1
    fi
fi

echo "OpenHIS restore ← ${BACKUP_ABS}  (profiles: ${PROFILES})"
(( DRY_RUN )) && note "DRY RUN — printing the command plan, executing nothing"

# ── 3. Stop the stack (volumes are kept) ──────────────────────────────────────
run "${COMPOSE[@]}" down

shopt -s nullglob

# ── 4. Volume tars (volumes/ + cold/) — restored while everything is down ────
#      redis-data is restored here, i.e. strictly before redis starts again.
for _tar in "${BACKUP_ABS}"/volumes/*.tgz "${BACKUP_ABS}"/cold/*.tgz; do
    _vol="$(basename "$_tar" .tgz)"
    _dir="$(dirname "$_tar")"
    ensure_volume "$_vol"
    run docker run --rm \
        -v "openhis_${_vol}:/dst" \
        -v "${_dir}:/src:ro" \
        alpine sh -c "find /dst -mindepth 1 -delete && tar xzf '/src/${_vol}.tgz' -C /dst"
done

# ── 5. SQLite artifacts (sqlite/<volume>__<dbfile>) ───────────────────────────
for _f in "${BACKUP_ABS}"/sqlite/*; do
    _base="$(basename "$_f")"
    _vol="${_base%%__*}"
    _db="${_base#*__}"
    ensure_volume "$_vol"
    run docker run --rm \
        -v "openhis_${_vol}:/data" \
        -v "${BACKUP_ABS}/sqlite:/src:ro" \
        alpine sh -c "rm -f '/data/${_db}' '/data/${_db}-wal' '/data/${_db}-shm' && cp '/src/${_base}' '/data/${_db}'"
done

# ── 6. Logical DB restores (skipped where a cold/ raw tar already won) ───────
DB_SERVICES=()

restore_postgres_core=0
if compgen -G "${BACKUP_ABS}/db/postgres/*" >/dev/null; then
    if cold_has pg-data; then
        note "db/postgres logical dumps skipped — cold/pg-data.tgz was restored physically"
    else
        restore_postgres_core=1
        DB_SERVICES+=(postgres)
    fi
fi

restore_openelis=0
if [[ -f "${BACKUP_ABS}/db/openelis/clinlims.dump" ]]; then
    if cold_has openelis-pg; then
        note "db/openelis logical dump skipped — cold/openelis-pg.tgz was restored physically"
    elif ! profile_active laboratory; then
        warn "clinlims dump present but the laboratory profile is not active — skipping" \
             "(enable the profile and re-run restore to apply it)"
    else
        restore_openelis=1
        DB_SERVICES+=(openelis-db)
    fi
fi

restore_odoo=0
if compgen -G "${BACKUP_ABS}/db/odoo/*.dump" >/dev/null; then
    if cold_has odoo-pg; then
        note "db/odoo logical dump skipped — cold/odoo-pg.tgz was restored physically"
    elif ! profile_active erp; then
        warn "odoo dump present but the erp profile is not active — skipping" \
             "(enable the profile and re-run restore to apply it)"
    else
        restore_odoo=1
        DB_SERVICES+=(odoo-db)
    fi
fi

restore_openmrs=0
if [[ -f "${BACKUP_ABS}/db/openmrs/openmrs.sql" ]]; then
    if cold_has openmrs-mysql; then
        note "db/openmrs logical dump skipped — cold/openmrs-mysql.tgz was restored physically"
    elif ! profile_active emr; then
        warn "openmrs dump present but the emr profile is not active — skipping" \
             "(enable the profile and re-run restore to apply it)"
    else
        restore_openmrs=1
        DB_SERVICES+=(openmrs-db)
    fi
fi

if (( ${#DB_SERVICES[@]} )); then
    note "starting database containers only, waiting on their healthchecks"
    run "${COMPOSE[@]}" up -d --wait "${DB_SERVICES[@]}"
fi

if (( restore_postgres_core )); then
    if [[ -f "${BACKUP_ABS}/db/postgres/globals.sql" ]]; then
        # shellcheck disable=SC2016  # $POSTGRES_USER expands inside the container
        if ! run_from "${BACKUP_ABS}/db/postgres/globals.sql" \
            "${COMPOSE[@]}" exec -T postgres sh -c 'exec psql -U "$POSTGRES_USER" -d postgres'; then
            warn "psql globals restore reported errors (pre-existing roles are harmless)"
        fi
    fi
    for _dump in "${BACKUP_ABS}"/db/postgres/*.dump; do
        _db="$(basename "$_dump" .dump)"
        run "${COMPOSE[@]}" exec -T postgres sh -c \
            "createdb -U \"\$POSTGRES_USER\" ${_db} 2>/dev/null || true"
        if ! run_from "$_dump" "${COMPOSE[@]}" exec -T postgres sh -c \
            "exec pg_restore --clean --if-exists -U \"\$POSTGRES_USER\" -d ${_db}"; then
            warn "pg_restore for '${_db}' reported errors — inspect the output above"
        fi
    done
fi

if (( restore_openelis )); then
    # shellcheck disable=SC2016  # $POSTGRES_USER (=postgres) expands in-container
    if ! run_from "${BACKUP_ABS}/db/openelis/clinlims.dump" \
        "${COMPOSE[@]}" exec -T openelis-db sh -c \
        'exec pg_restore --clean --if-exists -U "$POSTGRES_USER" -d clinlims'; then
        warn "pg_restore for 'clinlims' reported errors — inspect the output above"
    fi
fi

if (( restore_odoo )); then
    for _dump in "${BACKUP_ABS}"/db/odoo/*.dump; do
        _db="$(basename "$_dump" .dump)"
        note "odoo: drop + recreate database '${_db}' before pg_restore"
        run "${COMPOSE[@]}" exec -T odoo-db sh -c \
            "dropdb -U \"\$POSTGRES_USER\" --if-exists ${_db}"
        run "${COMPOSE[@]}" exec -T odoo-db sh -c \
            "createdb -U \"\$POSTGRES_USER\" ${_db}"
        if ! run_from "$_dump" "${COMPOSE[@]}" exec -T odoo-db sh -c \
            "exec pg_restore -U \"\$POSTGRES_USER\" -d ${_db}"; then
            warn "pg_restore for '${_db}' reported errors — inspect the output above"
        fi
    done
fi

if (( restore_openmrs )); then
    # shellcheck disable=SC2016  # $MYSQL_ROOT_PASSWORD expands inside the container
    if ! run_from "${BACKUP_ABS}/db/openmrs/openmrs.sql" \
        "${COMPOSE[@]}" exec -T openmrs-db sh -c \
        'exec mysql -u root -p"$MYSQL_ROOT_PASSWORD" openmrs'; then
        warn "mysql restore for 'openmrs' reported errors — inspect the output above"
    fi
fi

# ── 7. Restart the full stack ─────────────────────────────────────────────────
run "${COMPOSE[@]}" up -d

cat <<EOF

Restore complete. Post-restore checklist:
  1. make health                       — wait until every container is healthy
  2. If the odoo DB was restored or recreated, re-run the OIDC init:
       docker compose -f compose/base.yml -f compose/profiles/erp.yml run --rm odoo-init
  3. If the backup was taken with --skip-rebuildable, OpenELIS rebuilds the
     Lucene index on first use (first searches may be slow).
  4. Orthanc: index (postgres pg_dump) and pixel data (orthanc-data) were
     restored from the same backup window — spot-check a study in OHIF.
  5. A keycloak/ realm export (if present) is NOT applied automatically:
       docker compose -p openhis exec keycloak \\
         /opt/keycloak/bin/kc.sh import --dir /tmp/keycloak-export
     (docker cp the files into the container first.)
EOF
