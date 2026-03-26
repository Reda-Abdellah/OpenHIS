"""
Profile Engine — reads x-openhis metadata from compose profile YAML files.

Each profile YAML may contain a top-level x-openhis extension block:

  x-openhis:
    profile: laboratory
    display_name: "Laboratory Suite"
    requires: [base]
    integrates_with: [emr]
    nginx_routes:
      - { path: /OpenELIS-Global/, upstream: "openelis:8080" }
    databases: [openelis]

The engine resolves dependencies and reports missing requirements.
"""
import os
from pathlib import Path
from typing import Optional

import yaml

COMPOSE_DIR = Path(__file__).parent.parent / "compose"
PROFILES_DIR = COMPOSE_DIR / "profiles"

AVAILABLE_PROFILES = ["emr", "laboratory", "erp", "imaging", "analytics", "legacy"]


def load_profile_meta(profile_name: str) -> Optional[dict]:
    """Load x-openhis metadata block from a profile YAML file."""
    path = PROFILES_DIR / f"{profile_name}.yml"
    if not path.exists():
        return None
    with open(path) as f:
        doc = yaml.safe_load(f)
    return doc.get("x-openhis", {})


def get_all_profiles() -> dict:
    """Return metadata for all available profiles."""
    result = {}
    for name in AVAILABLE_PROFILES:
        meta = load_profile_meta(name)
        if meta:
            result[name] = meta
    return result


def resolve_compose_files(profiles: list[str]) -> list[str]:
    """
    Given a list of profile names, return the ordered list of
    -f flags needed for docker compose.
    """
    files = [str(COMPOSE_DIR / "base.yml")]
    seen = set()
    for p in profiles:
        if p in seen:
            continue
        seen.add(p)
        path = PROFILES_DIR / f"{p}.yml"
        if path.exists():
            files.append(str(path))
        else:
            raise ValueError(f"Unknown profile: {p}")
    return files


def check_dependencies(profiles: list[str]) -> list[str]:
    """Return a list of unmet dependency warnings."""
    warnings = []
    for p in profiles:
        meta = load_profile_meta(p)
        if not meta:
            continue
        requires = meta.get("requires", [])
        for req in requires:
            if req == "base":
                continue  # base is always included
            if req not in profiles:
                warnings.append(
                    f"Profile '{p}' recommends '{req}' — it is not in the active set"
                )
    return warnings


def get_nginx_routes(profiles: list[str]) -> list[dict]:
    """Collect all nginx_routes from the active profiles."""
    routes = []
    for p in profiles:
        meta = load_profile_meta(p)
        if meta:
            routes.extend(meta.get("nginx_routes", []))
    return routes


def estimate_ram_mb(profiles: list[str]) -> int:
    """Rough RAM estimate in MB for the active profile set."""
    costs = {
        "base": 512,         # postgres, nginx, admin, mpi, hub, hl7, keycloak
        "emr": 2048,         # OpenMRS is heavy
        "laboratory": 1024,
        "erp": 1024,
        "imaging": 1536,     # Orthanc + OHIF + RIS + AI
        "analytics": 256,
        "legacy": 512,
    }
    total = costs.get("base", 512)
    for p in profiles:
        total += costs.get(p, 256)
    return total
