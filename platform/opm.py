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
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
import requests
import yaml

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_env() -> dict:
    """Parse .env file into a dict."""
    env: dict = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_env(env: dict) -> None:
    """Write dict back to .env (preserving comments is not attempted)."""
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")


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
def cli():
    """OpenHIS Platform Manager — manage profiles and deployments."""


# ── opm init ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--non-interactive", is_flag=True, help="Use defaults without prompting")
def init(non_interactive: bool):
    """First-run wizard: choose profiles, set passwords, write .env."""
    click.echo("\n  OpenHIS Platform Manager — first-run setup\n")

    if ENV_FILE.exists() and not non_interactive:
        if not click.confirm(f"  .env already exists at {ENV_FILE}. Overwrite?"):
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

    # Passwords
    if non_interactive:
        env["POSTGRES_PASSWORD"] = "changeme"
        env["ADMIN_PASS"] = "admin123"
        env["KEYCLOAK_ADMIN_PASSWORD"] = "admin"
        env["KEYCLOAK_CLIENT_SECRET"] = "openhis-platform-secret"
    else:
        click.echo()
        env["POSTGRES_PASSWORD"] = click.prompt(
            "  PostgreSQL password", default="changeme", hide_input=True, confirmation_prompt=True
        )
        env["ADMIN_PASS"] = click.prompt(
            "  Admin panel password", default="admin123", hide_input=True, confirmation_prompt=True
        )
        env["KEYCLOAK_ADMIN_PASSWORD"] = click.prompt(
            "  Keycloak admin password", default="admin", hide_input=True, confirmation_prompt=True
        )
        env["KEYCLOAK_CLIENT_SECRET"] = click.prompt(
            "  Keycloak client secret", default="openhis-platform-secret", hide_input=True
        )

    env["POSTGRES_USER"] = "openhis"
    env["ADMIN_USER"] = "admin"
    env["KEYCLOAK_ADMIN"] = "admin"

    _write_env(env)
    click.echo(f"\n  Wrote {ENV_FILE}")

    # Render nginx config
    nginx_render(chosen)
    click.echo("  Rendered infra/nginx/nginx.conf")

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
