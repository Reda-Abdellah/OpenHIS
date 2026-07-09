"""
Offline self-test for scripts/backup.sh and scripts/restore.sh.

No Docker daemon and no running stack required: both scripts support
--dry-run, which prints the exact command plan without executing anything.

The completeness tests are *config-driven*: the expected set of named volumes
and databases is parsed straight out of compose/base.yml +
compose/profiles/*.yml (and infra/postgres/init-databases.sh for the shared
postgres). Adding a `volumes:` entry or a new DB container to compose without
teaching backup.sh about it FAILS these tests.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SH = REPO_ROOT / "scripts" / "backup.sh"
RESTORE_SH = REPO_ROOT / "scripts" / "restore.sh"
COMPOSE_FILES = [REPO_ROOT / "compose" / "base.yml"] + sorted(
    (REPO_ROOT / "compose" / "profiles").glob("*.yml")
)
INIT_DATABASES_SH = REPO_ROOT / "infra" / "postgres" / "init-databases.sh"

# Volumes that backup.sh may intentionally skip (rebuildable artifacts).
SKIP_ALLOWLIST = {"openelis-lucene"}


# ── Compose parsing helpers (yaml.safe_load — no `docker compose config`) ────

def _load_compose() -> list[dict]:
    return [yaml.safe_load(f.read_text()) for f in COMPOSE_FILES]


def _declared_volumes() -> set[str]:
    vols: set[str] = set()
    for doc in _load_compose():
        vols |= set((doc.get("volumes") or {}).keys())
    return vols


def _service_env(cfg: dict) -> dict:
    env = cfg.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return env


def _database_services() -> dict[str, str]:
    """Services that declare a database via POSTGRES_DB / MYSQL_DATABASE."""
    found: dict[str, str] = {}
    for doc in _load_compose():
        for svc, cfg in (doc.get("services") or {}).items():
            env = _service_env(cfg)
            if "POSTGRES_DB" in env:
                found[svc] = "postgres"
            elif "MYSQL_DATABASE" in env:
                found[svc] = "mysql"
    return found


def _database_volumes() -> set[str]:
    """Named volumes mounted by the database services above (raw data dirs)."""
    db_services = _database_services()
    vols: set[str] = set()
    for doc in _load_compose():
        declared = set((doc.get("volumes") or {}).keys())
        for svc, cfg in (doc.get("services") or {}).items():
            if svc not in db_services:
                continue
            for mount in cfg.get("volumes") or []:
                if isinstance(mount, str):
                    src = mount.split(":", 1)[0]
                    if src in declared:
                        vols.add(src)
    return vols


def _shared_postgres_databases() -> set[str]:
    """orthanc (POSTGRES_DB) + every CREATE DATABASE in init-databases.sh."""
    base = yaml.safe_load((REPO_ROOT / "compose" / "base.yml").read_text())
    dbs = {_service_env(base["services"]["postgres"])["POSTGRES_DB"]}
    for line in INIT_DATABASES_SH.read_text().splitlines():
        if "CREATE DATABASE" in line:
            dbs.add(line.split("CREATE DATABASE", 1)[1].split("'")[0].strip())
    return dbs


# ── Script invocation ─────────────────────────────────────────────────────────

def _run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("OPENHIS_PROFILES", None)  # deterministic regardless of local .env
    env.pop("BACKUP_DIR", None)
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=60,
    )


@pytest.fixture(scope="module")
def hot_plan() -> str:
    proc = _run_script(BACKUP_SH, "--dry-run", "--all-profiles")
    assert proc.returncode == 0, f"backup.sh --dry-run failed:\n{proc.stderr}"
    return proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def cold_plan() -> str:
    proc = _run_script(BACKUP_SH, "--dry-run", "--all-profiles", "--cold")
    assert proc.returncode == 0, f"backup.sh --cold --dry-run failed:\n{proc.stderr}"
    return proc.stdout + proc.stderr


def _covered(volume: str, plan: str) -> bool:
    # Either the volume is mounted by a backup container ("openhis_<vol>:")
    # or the plan explicitly documents logical-dump coverage for it.
    return f"openhis_{volume}:" in plan or f"volume {volume} covered" in plan


# ── Static checks ─────────────────────────────────────────────────────────────

def test_scripts_are_executable() -> None:
    for script in (BACKUP_SH, RESTORE_SH):
        assert script.exists(), f"{script} missing"
        assert os.access(script, os.X_OK), f"{script} is not executable (chmod +x)"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean() -> None:
    proc = subprocess.run(
        ["shellcheck", "-x", str(BACKUP_SH), str(RESTORE_SH)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"shellcheck findings:\n{proc.stdout}{proc.stderr}"


# ── Backup plan completeness (config-driven) ─────────────────────────────────

def test_compose_declares_expected_volume_baseline() -> None:
    # Sanity guard: if compose parsing ever returns an empty/odd set the
    # completeness test below would pass vacuously.
    volumes = _declared_volumes()
    assert {"pg-data", "redis-data", "openmrs-mysql", "openelis-pg"} <= volumes
    assert len(volumes) >= 17


def test_hot_plan_covers_every_declared_volume(hot_plan: str) -> None:
    missing = {
        vol
        for vol in _declared_volumes() - SKIP_ALLOWLIST
        if not _covered(vol, hot_plan)
    }
    assert not missing, (
        f"backup.sh --dry-run does not cover compose volumes: {sorted(missing)} — "
        "a volume was added to compose without updating scripts/backup.sh"
    )


def test_hot_plan_includes_rebuildable_volume_by_default(hot_plan: str) -> None:
    assert "openhis_openelis-lucene:" in hot_plan


def test_skip_rebuildable_omits_lucene_volume() -> None:
    proc = _run_script(BACKUP_SH, "--dry-run", "--all-profiles", "--skip-rebuildable")
    assert proc.returncode == 0, proc.stderr
    assert "openhis_openelis-lucene:" not in proc.stdout + proc.stderr


def test_hot_plan_dumps_every_compose_database_service(hot_plan: str) -> None:
    dump_tool = {"postgres": "pg_dump", "mysql": "mysqldump"}
    for svc, engine in _database_services().items():
        lines = [ln for ln in hot_plan.splitlines() if f"exec -T {svc} " in ln]
        assert any(dump_tool[engine] in ln for ln in lines), (
            f"no {dump_tool[engine]} planned for compose DB service '{svc}' — "
            "a database container was added without updating scripts/backup.sh"
        )


def test_hot_plan_dumps_every_shared_postgres_database(hot_plan: str) -> None:
    pg_lines = [
        ln for ln in hot_plan.splitlines()
        if "exec -T postgres " in ln and "pg_dump" in ln
    ]
    assert any("pg_dumpall" in ln for ln in pg_lines), "missing pg_dumpall (roles)"
    for db in _shared_postgres_databases():
        assert any(db in ln for ln in pg_lines), (
            f"no pg_dump planned for shared-postgres database '{db}' — "
            "keep CORE_PG_DATABASES in scripts/backup.sh in sync with "
            "infra/postgres/init-databases.sh"
        )


def test_hot_plan_checkpoints_redis_before_tar(hot_plan: str) -> None:
    assert "BGSAVE" in hot_plan
    assert hot_plan.index("BGSAVE") < hot_plan.index("openhis_redis-data:"), (
        "redis-data must be tarred after the BGSAVE checkpoint"
    )


def test_cold_plan_tars_raw_database_volumes(cold_plan: str) -> None:
    raw_volumes = _database_volumes()
    assert raw_volumes, "could not derive DB volumes from compose"
    for vol in raw_volumes:
        assert f"openhis_{vol}:" in cold_plan, (
            f"--cold plan does not tar raw DB volume '{vol}'"
        )
    assert " down " in cold_plan or cold_plan.rstrip().endswith("down"), (
        "--cold must stop the stack before snapshotting raw volumes"
    )


def test_plans_never_use_ci_override(hot_plan: str, cold_plan: str) -> None:
    assert "overrides/ci.yml" not in hot_plan
    assert "overrides/ci.yml" not in cold_plan


# ── Restore plan (fixture-driven) ─────────────────────────────────────────────

FIXTURE_ARTIFACTS: dict[str, bytes] = {
    "volumes/redis-data.tgz": b"tgz-placeholder-redis",
    "volumes/orthanc-data.tgz": b"tgz-placeholder-orthanc",
    "sqlite/admin-data__admin.db": b"sqlite-placeholder",
    "db/postgres/globals.sql": b"-- roles",
    "db/postgres/orthanc.dump": b"PGDMP-orthanc",
    "db/openelis/clinlims.dump": b"PGDMP-clinlims",
    "db/odoo/odoo.dump": b"PGDMP-odoo",
    "db/openmrs/openmrs.sql": b"-- mysqldump",
}

# Each artifact must produce at least one restore command containing this token.
ARTIFACT_EXPECTED_TOKEN = {
    "volumes/redis-data.tgz": "openhis_redis-data:",
    "volumes/orthanc-data.tgz": "openhis_orthanc-data:",
    "sqlite/admin-data__admin.db": "openhis_admin-data:",
    "db/postgres/globals.sql": "globals.sql",
    "db/postgres/orthanc.dump": "orthanc.dump",
    "db/openelis/clinlims.dump": "clinlims.dump",
    "db/odoo/odoo.dump": "odoo.dump",
    "db/openmrs/openmrs.sql": "openmrs.sql",
}


def _make_fixture_backup(root: Path) -> Path:
    backup_dir = root / "20260101T000000Z"
    sums: list[str] = []
    for rel, payload in FIXTURE_ARTIFACTS.items():
        path = backup_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        sums.append(f"{hashlib.sha256(payload).hexdigest()}  {rel}")
    (backup_dir / "SHA256SUMS").write_text("\n".join(sorted(sums)) + "\n")
    (backup_dir / "manifest.json").write_text(
        json.dumps({"backup_format": 1, "mode": "hot", "git_sha": "fixture"})
    )
    return backup_dir


def test_restore_dry_run_plans_one_command_per_artifact(tmp_path: Path) -> None:
    backup_dir = _make_fixture_backup(tmp_path)
    proc = _run_script(RESTORE_SH, "--dry-run", "--all-profiles", str(backup_dir))
    plan = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"restore.sh --dry-run failed:\n{plan}"
    assert "Checksums OK" in plan

    command_lines = [ln for ln in plan.splitlines() if ln.startswith("+ ")]
    for rel, token in ARTIFACT_EXPECTED_TOKEN.items():
        assert any(token in ln for ln in command_lines), (
            f"no restore command planned for artifact '{rel}' (token '{token}')"
        )

    # DBs restored only after their containers pass healthchecks.
    assert "--wait" in plan
    # Symmetric semantics from the spec:
    assert "pg_restore --clean --if-exists" in plan.replace("\\", "")
    assert "dropdb" in plan and "createdb" in plan  # odoo drop + recreate
    assert "mysql" in plan
    # redis-data is restored while the stack is down, before `up -d`.
    assert plan.index("openhis_redis-data:") < plan.rindex("up -d")


def test_restore_refuses_on_bad_sha256(tmp_path: Path) -> None:
    backup_dir = _make_fixture_backup(tmp_path)
    (backup_dir / "db" / "postgres" / "orthanc.dump").write_bytes(b"TAMPERED")
    proc = _run_script(RESTORE_SH, "--dry-run", "--all-profiles", str(backup_dir))
    assert proc.returncode != 0, "restore.sh must refuse a tampered backup"
    combined = proc.stdout + proc.stderr
    assert "FAILED" in combined and "refusing to restore" in combined
    # Refusal must come before any planned command.
    assert "+ " not in proc.stdout


def test_restore_requires_valid_backup_dir(tmp_path: Path) -> None:
    proc = _run_script(RESTORE_SH, "--dry-run", str(tmp_path / "nope"))
    assert proc.returncode != 0

    empty = tmp_path / "empty"
    empty.mkdir()
    proc = _run_script(RESTORE_SH, "--dry-run", str(empty))
    assert proc.returncode != 0
    assert "manifest.json" in proc.stdout + proc.stderr
