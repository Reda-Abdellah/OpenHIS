"""
nginx_gen — renders infra/nginx/nginx.conf from nginx.conf.j2 and
the active profile set, then optionally reloads nginx in-container.

Usage (standalone):
    python platform/nginx_gen.py --profiles emr,laboratory,imaging

Called by OPM automatically on enable/disable.
"""
import subprocess
import sys
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from profile_engine import get_all_profiles

REPO_ROOT = Path(__file__).parent.parent
TEMPLATE_PATH = REPO_ROOT / "infra" / "nginx" / "nginx.conf.j2"
OUTPUT_PATH = REPO_ROOT / "infra" / "nginx" / "nginx.conf"

# Profiles handled by named blocks in the template (skip generic loop)
NAMED_PROFILES = {"emr", "laboratory", "erp", "imaging", "analytics"}


def _slug(addr: str) -> str:
    """Turn 'service:port' into a valid nginx upstream name."""
    return addr.replace(":", "_").replace("-", "_").replace(".", "_")


def build_context(active_profiles: list[str]) -> dict:
    """
    Build the Jinja2 template context for a given list of active profiles.

    Returns:
        dict with keys:
            active_profiles  — list of profile names
            extra_upstreams  — [{name, addr}] for profiles not in NAMED_PROFILES
            extra_routes     — [{name, path, label}] same set
    """
    all_meta = get_all_profiles()

    extra_upstreams = []
    extra_routes = []
    seen_upstreams: set[str] = set()

    for p in active_profiles:
        if p in NAMED_PROFILES:
            continue
        meta = all_meta.get(p, {})
        for route in meta.get("nginx_routes", []):
            path: str = route.get("path", "")
            upstream_addr: str = route.get("upstream", "")
            if not path or not upstream_addr:
                continue
            name = _slug(upstream_addr)
            if name not in seen_upstreams:
                seen_upstreams.add(name)
                extra_upstreams.append({"name": name, "addr": upstream_addr})
            extra_routes.append({
                "name": name,
                "path": path if path.endswith("/") else path + "/",
                "label": f"{p} — {path}",
            })

    return {
        "active_profiles": active_profiles,
        "extra_upstreams": extra_upstreams,
        "extra_routes": extra_routes,
    }


def render(active_profiles: list[str], output: Optional[Path] = None) -> str:
    """Render nginx.conf.j2 for the given profiles. Returns rendered text."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_PATH.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template(TEMPLATE_PATH.name)
    context = build_context(active_profiles)
    rendered = template.render(**context)

    dest = output or OUTPUT_PATH
    dest.write_text(rendered)
    return rendered


def reload_nginx(container: str = "nginx") -> bool:
    """Send nginx -s reload inside the running container. Returns True on success."""
    result = subprocess.run(
        ["docker", "exec", container, "nginx", "-s", "reload"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[nginx_gen] reload failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate nginx.conf from active profiles")
    parser.add_argument(
        "--profiles",
        default="emr,laboratory,erp,imaging,analytics",
        help="Comma-separated list of active profiles",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Also reload nginx container after writing config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rendered config to stdout instead of writing to disk",
    )
    args = parser.parse_args()

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]

    if args.dry_run:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_PATH.parent)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        template = env.get_template(TEMPLATE_PATH.name)
        print(template.render(**build_context(profiles)))
    else:
        render(profiles)
        print(f"[nginx_gen] wrote {OUTPUT_PATH}")
        if args.reload:
            ok = reload_nginx()
            print(f"[nginx_gen] nginx reload {'ok' if ok else 'FAILED'}")
