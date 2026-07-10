#!/usr/bin/env python3
"""
OPM — OpenHIS Platform Manager

Profile-driven deployment CLI for the OpenHIS platform.

Quick start:
    python platform/opm.py init
    python platform/opm.py enable emr laboratory
    python platform/opm.py status

All docker compose operations run from the project root.
OPM wraps the Makefile logic — it does not replace it.
"""
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
import requests
import yaml

#: Single source of truth for the package version — pyproject.toml reads it
#: via [tool.hatch.version] path = "opm.py".
__version__ = "0.6.0-alpha.1"

# ── Path resolution ────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
COMPOSE_DIR = REPO_ROOT / "compose"
ENV_FILE = REPO_ROOT / ".env"
ADMIN_URL = os.environ.get("ADMIN_URL", "http://localhost/admin")

sys.path.insert(0, str(Path(__file__).parent))
from profile_engine import (
    AVAILABLE_PROFILES,
    check_dependencies,
    estimate_ram_mb,
    resolve_compose_files,
)
from nginx_gen import render as nginx_render, reload_nginx
from infra_render import DEV_DEFAULTS, InfraRenderError, render_templates


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_env(path: Path = ENV_FILE) -> dict:
    """Parse a .env-style file into a dict."""
    env: dict = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_env(env: dict, path: Path = ENV_FILE) -> None:
    """
    Write *env* to *path*, preserving comments and blank lines.

    The existing file (or .env.example on first run) is used as the layout:
    assignment lines whose key appears in *env* get the new value in place;
    every other line — comments, blanks, untouched assignments — is kept
    verbatim.  Keys not present in the layout are appended at the end.
    """
    layout = path if path.exists() else REPO_ROOT / ".env.example"
    base_lines = layout.read_text().splitlines() if layout.exists() else []

    remaining = dict(env)
    out: list[str] = []
    for line in base_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    if remaining:
        if out:
            out.append("")
        out.append("# ── Added by opm init ────────────────────────────────────────────────────────")
        out.extend(f"{k}={v}" for k, v in remaining.items())

    path.write_text("\n".join(out) + "\n")


def _active_profiles() -> list[str]:
    env = _read_env()
    raw = env.get("OPENHIS_PROFILES", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _set_profiles(profiles: list[str]) -> None:
    env = _read_env()
    env["OPENHIS_PROFILES"] = ",".join(profiles)
    _write_env(env)


def _compose_cmd(profiles: list[str]) -> list[str]:
    """Build the docker compose command prefix for given profiles."""
    files = resolve_compose_files(profiles)
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", f]
    return cmd


def _run(cmd: list[str], **kwargs) -> int:
    click.echo(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), **kwargs)
    return result.returncode


def _notify_registry(profiles: list[str]) -> None:
    """Tell admin service which profiles are active (best-effort)."""
    try:
        requests.post(
            f"{ADMIN_URL}/api/registry/sync",
            json={"active_profiles": profiles},
            timeout=3,
        )
    except Exception:
        pass  # Admin may not be running yet; silent failure is fine


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(__version__, prog_name="opm")
def cli():
    """OpenHIS Platform Manager — manage profiles and deployments."""


# ── opm init ─────────────────────────────────────────────────────────────────

_CHANGEME_SENTINEL = "CHANGE_ME_BEFORE_DEPLOY"

#: Every secret `opm init` must populate.  docker-compose falls back to weak,
#: publicly known dev defaults when any of these is unset — init must leave
#: none of them missing (F#45, F#46).
#:
#: OPENMRS_PASSWORD / OPENELIS_PASSWORD seed the real OpenMRS / OpenELIS
#: admin accounts (compose fallbacks `Admin123` / `adminADMIN!` are public
#: knowledge, and the OpenELIS UI is host-published on :8082).
#: REDIS_PASSWORD: empty = dev no-auth mode; init generates a value, which
#: enables Redis AUTH on initialized stacks (all compose REDIS_URLs and the
#: redis healthcheck already embed/handle it).
_REQUIRED_SECRETS = [
    "POSTGRES_PASSWORD", "MPI_DB_PASS",
    "ADMIN_PASS",
    "REDIS_PASSWORD",
    "KEYCLOAK_ADMIN_PASSWORD", "KEYCLOAK_CLIENT_SECRET",
    "INTEGRATION_HUB_KC_CLIENT_SECRET", "HL7_KC_CLIENT_SECRET",
    "ANALYTICS_KC_CLIENT_SECRET", "RIS_KC_CLIENT_SECRET",
    "ORTHANC_KC_CLIENT_SECRET",
    "PATIENT_PORTAL_KC_CLIENT_SECRET", "OPENMRS_KC_CLIENT_SECRET",
    "OPENMRS_PASSWORD", "OPENELIS_PASSWORD",
    "ODOO_MASTER_PASS", "ODOO_ADMIN_PASS", "ODOO_OIDC_SECRET",
    "OPENELIS_OIDC_SECRET",
]

#: Legacy per-secret CLI flags / env vars kept for backward compatibility.
_LEGACY_FLAG_KEYS = {
    "POSTGRES_PASSWORD": "--postgres-pass / OPENHIS_POSTGRES_PASS",
    "ADMIN_PASS": "--admin-pass / OPENHIS_ADMIN_PASS",
    "KEYCLOAK_ADMIN_PASSWORD": "--keycloak-pass / OPENHIS_KEYCLOAK_PASS",
    "KEYCLOAK_CLIENT_SECRET": "--keycloak-secret / OPENHIS_KEYCLOAK_SECRET",
}


def _check_secret_strength(value: str) -> bool:
    """True when *value* has ≥16 chars and ≥3 character classes."""
    if len(value) < 16:
        return False
    classes = sum((
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ))
    return classes >= 3


def _generate_secret() -> str:
    """Strong random secret (~43 chars urlsafe); retries until it passes the strength check."""
    while True:
        candidate = secrets.token_urlsafe(32)
        if _check_secret_strength(candidate):
            return candidate


def _validate_env_file(path: Path) -> list[str]:
    """
    Re-read *path* from disk and return a list of problems.

    Required secrets must be present, non-empty, not the CHANGEME sentinel
    and pass the strength check; no other variable may hold the sentinel.
    """
    env = _read_env(path)
    problems: list[str] = []
    for key in _REQUIRED_SECRETS:
        value = env.get(key, "")
        if not value:
            problems.append(f"{key} is missing or empty")
        elif value == _CHANGEME_SENTINEL:
            problems.append(f"{key} is still {_CHANGEME_SENTINEL}")
        elif not _check_secret_strength(value):
            problems.append(f"{key} is too weak (need ≥16 chars and ≥3 character classes)")
    for key, value in env.items():
        if key not in _REQUIRED_SECRETS and value == _CHANGEME_SENTINEL:
            problems.append(f"{key} is still {_CHANGEME_SENTINEL}")
    return problems


@cli.command()
@click.option("--non-interactive", is_flag=True,
              help="No prompts: secrets come from flags/env vars or --auto-generate; "
                   "fails if any secret would be left unset.")
@click.option("--auto-generate/--prompt", "auto_generate", default=True,
              help="Auto-generate strong random values for missing secrets (default), "
                   "or prompt for each one.")
@click.option("--postgres-pass", envvar="OPENHIS_POSTGRES_PASS", default=None,
              help="PostgreSQL password (optional; overrides auto-generation).")
@click.option("--admin-pass", envvar="OPENHIS_ADMIN_PASS", default=None,
              help="Admin panel password (optional; overrides auto-generation).")
@click.option("--keycloak-pass", envvar="OPENHIS_KEYCLOAK_PASS", default=None,
              help="Keycloak admin password (optional; overrides auto-generation).")
@click.option("--keycloak-secret", envvar="OPENHIS_KEYCLOAK_SECRET", default=None,
              help="Keycloak client secret (optional; overrides auto-generation).")
@click.option("--validate/--no-validate", "validate", default=True,
              help="After writing, re-read .env from disk and fail on empty, "
                   "sentinel or weak secret values (default: on).")
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Write .env and rendered infra files under this directory instead "
                   "of the repo root (dry-run / testing; skips nginx render).")
def init(non_interactive: bool, auto_generate: bool, postgres_pass, admin_pass,
         keycloak_pass, keycloak_secret, validate: bool, output_dir: Optional[Path]):
    """First-run wizard: choose profiles, set ALL secrets, write .env, render infra."""
    click.echo("\n  OpenHIS Platform Manager — first-run setup\n")

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
    env_path = (output_dir / ".env") if output_dir else ENV_FILE

    if env_path.exists() and not non_interactive:
        if not click.confirm(f"  .env already exists at {env_path}. Overwrite?"):
            click.echo("  Aborted.")
            return

    env: dict = {}

    # Profile selection
    click.echo("  Available profiles:")
    for i, p in enumerate(AVAILABLE_PROFILES, 1):
        click.echo(f"    {i}. {p}")

    if non_interactive:
        chosen = ["emr", "laboratory", "erp", "imaging", "analytics"]
    else:
        raw = click.prompt(
            "\n  Profiles to enable (comma-separated, or 'all')",
            default="emr,laboratory,imaging,analytics",
        )
        if raw.strip().lower() == "all":
            chosen = list(AVAILABLE_PROFILES)
        else:
            chosen = [p.strip() for p in raw.split(",") if p.strip() in AVAILABLE_PROFILES]

    env["OPENHIS_PROFILES"] = ",".join(chosen)

    # RAM estimate
    ram = estimate_ram_mb(chosen)
    click.echo(f"\n  Estimated RAM requirement: {ram} MB ({ram // 1024} GB)")

    # ── Secrets ──
    # Resolution order per key: legacy flag → env var of the same name →
    # auto-generate (default) → interactive prompt.  In --non-interactive
    # mode without --auto-generate, every key must be supplied explicitly —
    # nothing may silently fall through to a compose `${VAR:-default}`.
    flag_values = {
        "POSTGRES_PASSWORD": postgres_pass,
        "ADMIN_PASS": admin_pass,
        "KEYCLOAK_ADMIN_PASSWORD": keycloak_pass,
        "KEYCLOAK_CLIENT_SECRET": keycloak_secret,
    }
    supplied: list[str] = []
    generated: list[str] = []
    prompted: list[str] = []
    missing: list[str] = []

    if not non_interactive and not auto_generate:
        click.echo()

    for key in _REQUIRED_SECRETS:
        value = flag_values.get(key) or os.environ.get(key)
        if value:
            supplied.append(key)
        elif auto_generate:
            value = _generate_secret()
            generated.append(key)
        elif non_interactive:
            missing.append(_LEGACY_FLAG_KEYS.get(key, f"{key} env var"))
            continue
        else:
            value = click.prompt(f"  {key}", hide_input=True)
            prompted.append(key)
        env[key] = value

    if missing:
        click.echo(
            "\n  ERROR: --non-interactive without --auto-generate requires every "
            "secret to be supplied explicitly.\n  Missing:\n"
            + "".join(f"    • {m}\n" for m in missing),
            err=True,
        )
        sys.exit(1)

    click.echo(
        f"\n  Secrets: {len(generated)} auto-generated, "
        f"{len(prompted)} prompted, {len(supplied)} supplied"
    )
    if generated:
        click.echo("  Auto-generated (values never shown): " + ", ".join(generated))

    env["POSTGRES_USER"] = "openhis"
    env["ADMIN_USER"] = "admin"
    env["KEYCLOAK_ADMIN"] = "admin"

    _write_env(env, env_path)
    click.echo(f"\n  Wrote {env_path}")

    # Validate the file actually written to disk — not the in-memory dict.
    if validate:
        problems = _validate_env_file(env_path)
        if problems:
            click.echo(
                f"\n  ERROR: {env_path} failed validation:\n"
                + "".join(f"    • {p}\n" for p in problems)
                + "  Fix the values above, or re-run with --no-validate to skip.",
                err=True,
            )
            sys.exit(1)
        click.echo("  Validation passed: no empty, sentinel, or weak secret values.")

    # Render nginx config (in-place only — meaningless for a dry-run dir)
    if output_dir is not None:
        click.echo("  Skipped nginx render (--output-dir set).")
    else:
        nginx_render(chosen)
        click.echo("  Rendered infra/nginx/nginx.conf")

    # Render secret-bearing infra templates (Keycloak realm + the matching
    # OpenMRS/OpenELIS consumer-side OIDC config) from .env
    try:
        rendered = render_templates(
            _read_env(env_path),
            out_root=(output_dir / "infra") if output_dir else None,
        )
    except InfraRenderError as exc:
        click.echo(f"\n  ERROR: {exc}", err=True)
        sys.exit(1)
    for path in rendered:
        click.echo(f"  Rendered {path}")

    click.echo("\n  Done. Run `opm up` or `make up` to start the stack.\n")


# ── opm enable ───────────────────────────────────────────────────────────────

@cli.command()
@click.argument("profiles", nargs=-1, required=True)
@click.option("--no-start", is_flag=True, help="Update config only, do not start containers")
def enable(profiles: tuple, no_start: bool):
    """Enable one or more profiles and start their containers."""
    active = _active_profiles()
    to_add = [p for p in profiles if p not in active]

    for p in to_add:
        if p not in AVAILABLE_PROFILES:
            click.echo(f"  Unknown profile: {p}. Available: {', '.join(AVAILABLE_PROFILES)}")
            sys.exit(1)

    if not to_add:
        click.echo("  All specified profiles are already enabled.")
        return

    new_active = active + to_add

    # Dependency check
    warnings = check_dependencies(new_active)
    for w in warnings:
        click.echo(f"  WARNING: {w}")

    # RAM estimate
    ram = estimate_ram_mb(new_active)
    click.echo(f"  RAM estimate after enable: {ram} MB")

    _set_profiles(new_active)
    click.echo(f"  Updated OPENHIS_PROFILES: {','.join(new_active)}")

    nginx_render(new_active)
    click.echo("  Regenerated nginx config")

    if not no_start:
        cmd = _compose_cmd(new_active)
        services: list[str] = []
        for p in to_add:
            profile_file = COMPOSE_DIR / "profiles" / f"{p}.yml"
            if profile_file.exists():
                with open(profile_file) as f:
                    doc = yaml.safe_load(f) or {}
                services.extend(doc.get("services", {}).keys())

        rc = _run(cmd + ["up", "-d"] + services)
        if rc != 0:
            click.echo(f"  docker compose exited with code {rc}", err=True)
            sys.exit(rc)

        reload_nginx()
        click.echo("  nginx reloaded")

    _notify_registry(new_active)
    click.echo(f"\n  Enabled: {', '.join(to_add)}\n")


# ── opm disable ──────────────────────────────────────────────────────────────

@cli.command()
@click.argument("profiles", nargs=-1, required=True)
@click.option("--remove-volumes", is_flag=True, help="Also remove named volumes (destructive!)")
def disable(profiles: tuple, remove_volumes: bool):
    """Disable one or more profiles and stop their containers."""
    active = _active_profiles()
    to_remove = [p for p in profiles if p in active]

    if not to_remove:
        click.echo("  None of the specified profiles are currently enabled.")
        return

    if remove_volumes:
        click.confirm(
            f"  This will delete volumes for {to_remove}. This cannot be undone. Continue?",
            abort=True,
        )

    new_active = [p for p in active if p not in to_remove]

    # Stop containers for each disabled profile
    all_services: list[str] = []
    for p in to_remove:
        profile_file = COMPOSE_DIR / "profiles" / f"{p}.yml"
        if profile_file.exists():
            with open(profile_file) as f:
                doc = yaml.safe_load(f) or {}
            all_services.extend(doc.get("services", {}).keys())

    if all_services:
        cmd = _compose_cmd(active)
        stop_cmd = cmd + ["stop"] + all_services
        _run(stop_cmd)
        if remove_volumes:
            _run(cmd + ["rm", "-f", "-v"] + all_services)

    _set_profiles(new_active)
    nginx_render(new_active)
    reload_nginx()

    _notify_registry(new_active)
    click.echo(f"\n  Disabled: {', '.join(to_remove)}\n")


# ── opm up ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--detach/--no-detach", default=True, help="Run in background (default: yes)")
def up(detach: bool):
    """Start all active profiles."""
    active = _active_profiles()
    if not active:
        click.echo("  No profiles enabled. Run `opm init` or `opm enable <profile>`.")
        sys.exit(1)

    cmd = _compose_cmd(active) + ["up"]
    if detach:
        cmd.append("-d")
    rc = _run(cmd)
    sys.exit(rc)


# ── opm down ─────────────────────────────────────────────────────────────────

@cli.command()
def down():
    """Stop and remove all running containers (keeps volumes)."""
    active = _active_profiles()
    if not active:
        click.echo("  No profiles active.")
        return
    rc = _run(_compose_cmd(active) + ["down"])
    sys.exit(rc)


# ── opm status ───────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show active profiles and live service health."""
    active = _active_profiles()
    ram = estimate_ram_mb(active)

    click.echo(f"\n  Active profiles: {', '.join(active) if active else '(none)'}")
    click.echo(f"  RAM estimate:    {ram} MB\n")

    # Try admin registry API
    try:
        resp = requests.get(f"{ADMIN_URL}/api/registry", timeout=4)
        if resp.ok:
            services = resp.json()
            _print_service_table(services)
            return
    except Exception:
        pass

    # Fallback: docker ps
    click.echo("  (Admin service unreachable — falling back to docker ps)\n")
    subprocess.run(
        ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        cwd=str(REPO_ROOT),
    )


def _print_service_table(services: list) -> None:
    col = [30, 12, 10, 20]
    header = f"  {'Service':<{col[0]}} {'Profile':<{col[1]}} {'Status':<{col[2]}} {'Last seen':<{col[3]}}"
    sep = "  " + "-" * (sum(col) + 3 * len(col))
    click.echo(header)
    click.echo(sep)
    for s in sorted(services, key=lambda x: (x.get("profile", ""), x.get("name", ""))):
        status_color = "green" if s.get("status") == "healthy" else "red"
        click.echo(
            f"  {s.get('name',''):<{col[0]}} "
            f"{s.get('profile',''):<{col[1]}} "
            + click.style(f"{s.get('status','unknown'):<{col[2]}}", fg=status_color)
            + f" {s.get('last_seen', '')[:19]:<{col[3]}}"
        )
    click.echo()


# ── opm upgrade ──────────────────────────────────────────────────────────────

@cli.command()
@click.argument("profiles", nargs=-1)
def upgrade(profiles: tuple):
    """
    Pull latest images and restart services one at a time.

    Waits for each service to pass its healthcheck before moving to the next.
    Stateful apps (OpenMRS, OpenELIS, Odoo) can take several minutes.
    """
    active = _active_profiles()
    target = list(profiles) if profiles else active

    for p in target:
        profile_file = COMPOSE_DIR / "profiles" / f"{p}.yml"
        if not profile_file.exists():
            click.echo(f"  Skipping unknown profile: {p}")
            continue

        with open(profile_file) as f:
            doc = yaml.safe_load(f) or {}
        svc_names = list(doc.get("services", {}).keys())

        click.echo(f"\n  Upgrading profile '{p}' ({len(svc_names)} services)...")
        cmd = _compose_cmd(active)

        for svc in svc_names:
            click.echo(f"    Pulling {svc}...")
            _run(cmd + ["pull", svc])
            click.echo(f"    Restarting {svc}...")
            rc = _run(cmd + ["up", "-d", "--no-deps", svc])
            if rc != 0:
                click.echo(f"    ERROR: {svc} failed to start (exit {rc})", err=True)
                if not click.confirm("    Continue with remaining services?"):
                    sys.exit(1)

    click.echo("\n  Upgrade complete.\n")


# ── opm config ───────────────────────────────────────────────────────────────

@cli.group()
def config():
    """Manage platform configuration via the admin API."""


@config.command("set")
@click.argument("service")
@click.argument("key")
@click.argument("value")
def config_set(service: str, key: str, value: str):
    """Set a config value via the admin service API."""
    url = f"{ADMIN_URL}/api/config/{key}"
    try:
        resp = requests.put(url, json={"value": value, "service": service}, timeout=5)
        resp.raise_for_status()
        click.echo(f"  Set {service}.{key} = {value}")
    except requests.RequestException as e:
        click.echo(f"  Failed to set config: {e}", err=True)
        sys.exit(1)


@config.command("get")
@click.argument("key")
def config_get(key: str):
    """Get a config value from the admin service API."""
    url = f"{ADMIN_URL}/api/config/{key}"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        click.echo(f"  {key} = {data.get('value', data)}")
    except requests.RequestException as e:
        click.echo(f"  Failed to get config: {e}", err=True)
        sys.exit(1)


@config.command("list")
def config_list():
    """List all config values from the admin service API."""
    url = f"{ADMIN_URL}/api/config"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        items = resp.json()
        for item in items:
            click.echo(f"  {item.get('key')} = {item.get('value')}")
    except requests.RequestException as e:
        click.echo(f"  Failed to list config: {e}", err=True)
        sys.exit(1)


# ── opm nginx ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--reload/--no-reload", default=True, help="Reload nginx after rendering")
@click.option("--dry-run", is_flag=True, help="Print to stdout, do not write file")
def nginx(reload: bool, dry_run: bool):
    """Regenerate nginx.conf from active profiles."""
    active = _active_profiles()
    if dry_run:
        from nginx_gen import build_context, TEMPLATE_PATH
        from jinja2 import Environment, FileSystemLoader
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_PATH.parent)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = env.get_template(TEMPLATE_PATH.name)
        click.echo(template.render(**build_context(active)))
        return

    nginx_render(active)
    click.echo(f"  Wrote infra/nginx/nginx.conf (profiles: {', '.join(active)})")

    if reload:
        ok = reload_nginx()
        click.echo(f"  nginx reload: {'ok' if ok else 'FAILED'}")


# ── opm render-infra ─────────────────────────────────────────────────────────

@cli.command("render-infra")
@click.option("--validate", is_flag=True,
              help="Check that every template variable is set; write nothing.")
@click.option("--env-file", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Read variables from this file instead of the repo .env.")
@click.option("--out-dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Write rendered files under this directory instead of infra/ (testing).")
def render_infra(validate: bool, env_file: Optional[Path], out_dir: Optional[Path]):
    """Render infra/**/*.j2 secret templates (except nginx) from .env values."""
    source = env_file if env_file is not None else ENV_FILE
    if not source.exists():
        click.echo(f"  ERROR: {source} not found — run `opm init` first.", err=True)
        sys.exit(1)

    try:
        outputs = render_templates(
            _read_env(source), out_root=out_dir, write=not validate
        )
    except InfraRenderError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        sys.exit(1)

    if validate:
        click.echo(f"  OK — {len(outputs)} template(s) renderable with values from {source}")
    else:
        for path in outputs:
            click.echo(f"  Rendered {path}")


# ── opm demo-render ──────────────────────────────────────────────────────────

@cli.command("demo-render")
@click.option("--out-dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Write rendered files under this directory instead of infra/ (testing).")
def demo_render(out_dir: Optional[Path]):
    """Render infra templates with well-known DEV-ONLY values (local demo only)."""
    click.echo(
        "  WARNING: rendering with publicly known dev-only secrets — "
        "local demo use only, NEVER in any real deployment."
    )
    try:
        outputs = render_templates(DEV_DEFAULTS, out_root=out_dir)
    except InfraRenderError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        sys.exit(1)
    for path in outputs:
        click.echo(f"  Rendered {path}")


# ── opm add-service ──────────────────────────────────────────────────────────

@cli.command("add-service")
@click.argument("name")
@click.option("--port", default=8020, help="Service HTTP port inside the container")
@click.option("--profile", default="base", help="Profile this service belongs to")
@click.option("--nginx-path", default=None, help="nginx location prefix (e.g. /my-service)")
def add_service(name: str, port: int, profile: str, nginx_path: Optional[str]):
    """Scaffold a new native FastAPI service from the standard template."""
    svc_dir = REPO_ROOT / "services" / name
    if svc_dir.exists():
        click.echo(f"  Directory {svc_dir} already exists. Aborting.")
        sys.exit(1)

    svc_dir.mkdir(parents=True)
    nginx_route = nginx_path or f"/{name}"

    # Minimal FastAPI skeleton
    (svc_dir / "main.py").write_text(f'''\
"""
{name} — OpenHIS native service.
Generated by opm add-service.
"""
import os
from fastapi import FastAPI

ROOT_PATH = os.environ.get("ROOT_PATH", "/{name}")
app = FastAPI(root_path=ROOT_PATH)


@app.get("/api/health")
async def health():
    return {{"status": "ok", "service": "{name}"}}
''')

    (svc_dir / "requirements.txt").write_text(
        "fastapi==0.110.0\nuvicorn[standard]==0.27.1\nhttpx==0.27.0\n"
    )

    (svc_dir / "Dockerfile").write_text(f'''\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
''')

    # Service manifest
    import json
    manifest = {
        "name": name,
        "port": port,
        "profile": profile,
        "nginx_path": nginx_route,
        "bus": {"publishes": [], "subscribes": []},
        "depends_on": [],
    }
    (svc_dir / "openhis.service.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    click.echo(f"  Created {svc_dir}/")
    click.echo(f"    main.py, requirements.txt, Dockerfile, openhis.service.json")
    click.echo(f"\n  Next steps:")
    click.echo(f"    1. Add '{name}' service to compose/profiles/{profile}.yml")
    click.echo(f"    2. Add nginx route to compose/profiles/{profile}.yml x-openhis block")
    click.echo(f"    3. Run: opm enable {profile}")


if __name__ == "__main__":
    cli()
