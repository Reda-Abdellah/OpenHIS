"""
Platform topology — read-only view of the OpenHIS deployment.

GET /api/platform/topology   — service dependency graph (nodes + edges)
GET /api/platform/profiles   — available profiles with RAM estimate + status
GET /api/platform/ram        — total RAM estimate for active profiles
"""
import os
import yaml
from fastapi import APIRouter
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/platform", tags=["platform"])

# Paths are resolved relative to the project root (two levels up from this file)
_HERE = os.path.dirname(__file__)
_COMPOSE_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "compose"))

_PROFILE_NAMES = ["emr", "laboratory", "erp", "imaging", "analytics"]

# Rough RAM estimates in MB (kept in sync with profile_engine.py)
_RAM_MB = {
    "base":       512,
    "emr":       2048,
    "laboratory": 1024,
    "erp":        1024,
    "imaging":    1536,
    "analytics":   256,
}


def _load_profile_meta(profile: str) -> dict:
    path = os.path.join(_COMPOSE_DIR, "profiles", f"{profile}.yml")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("x-openhis", {})


def _active_profiles() -> list[str]:
    env_file = os.path.normpath(os.path.join(_HERE, "..", "..", "..", ".env"))
    if not os.path.exists(env_file):
        return []
    for line in open(env_file).read().splitlines():
        line = line.strip()
        if line.startswith("OPENHIS_PROFILES="):
            raw = line.split("=", 1)[1].strip()
            return [p.strip() for p in raw.split(",") if p.strip()]
    return []


@router.get("/topology")
def topology():
    """
    Returns a node/edge graph describing service dependencies.

    nodes: [{id, label, profile, status, nginx_path}]
    edges: [{source, target, label}]
    """
    with get_db() as db:
        rows = rows_to_list(
            db.execute("SELECT name, profile, nginx_path, status FROM service_registry").fetchall()
        )

    # Build nodes from registry
    nodes = [
        {
            "id":         r["name"],
            "label":      r["name"],
            "profile":    r["profile"],
            "status":     r["status"],
            "nginx_path": r["nginx_path"],
        }
        for r in rows
    ]

    # Static edges derived from known integration points
    edges = [
        {"source": "integration-hub", "target": "openmrs",    "label": "FHIR R4 poll"},
        {"source": "integration-hub", "target": "openelis",   "label": "FHIR R4 push"},
        {"source": "integration-hub", "target": "redis",      "label": "publish events"},
        {"source": "mpi",             "target": "redis",      "label": "subscribe"},
        {"source": "analytics",       "target": "redis",      "label": "subscribe"},
        {"source": "hl7",             "target": "openmrs",    "label": "ADT/ORU"},
        {"source": "admin",           "target": "keycloak",   "label": "JWKS"},
        {"source": "admin",           "target": "redis",      "label": "SSE stream"},
        {"source": "nginx",           "target": "admin",      "label": "proxy"},
        {"source": "nginx",           "target": "mpi",        "label": "proxy"},
        {"source": "nginx",           "target": "keycloak",   "label": "proxy"},
        {"source": "openmrs",         "target": "mpi",        "label": "crossref"},
        {"source": "openelis",        "target": "mpi",        "label": "crossref"},
    ]

    return {"nodes": nodes, "edges": edges}


@router.get("/profiles")
def profiles():
    """List all profiles with metadata, RAM estimate, and whether they are active."""
    active = _active_profiles()
    result = []
    for p in _PROFILE_NAMES:
        meta = _load_profile_meta(p)
        result.append({
            "name":         p,
            "display_name": meta.get("display_name", p.title()),
            "description":  meta.get("description", ""),
            "active":       p in active,
            "ram_mb":       _RAM_MB.get(p, 0),
            "requires":     meta.get("requires", []),
            "integrates":   meta.get("integrates_with", []),
            "nginx_routes": meta.get("nginx_routes", []),
        })
    return result


@router.get("/ram")
def ram_estimate():
    """Total RAM estimate for the current active profile set."""
    active = _active_profiles()
    total = _RAM_MB.get("base", 512)
    for p in active:
        total += _RAM_MB.get(p, 0)
    return {
        "active_profiles": active,
        "total_mb":        total,
        "total_gb":        round(total / 1024, 1),
    }
