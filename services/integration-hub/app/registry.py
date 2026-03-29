"""
Service registry loader.

Reads ``openhis.service.json`` manifests mounted into the container at
SERVICES_REGISTRY_DIR (default: /app/manifests).  Falls back to an
empty list if the directory is absent so the service can start without
the mounts during local development.
"""
import json
import logging
import os
from pathlib import Path
from typing import List

log = logging.getLogger("hub.registry")

_REGISTRY_DIR = os.getenv("SERVICES_REGISTRY_DIR", "/app/manifests")

_services: List[dict] = []


def load() -> None:
    """Load all *.json manifests from the registry directory."""
    global _services
    d = Path(_REGISTRY_DIR)
    if not d.is_dir():
        log.warning("Registry dir %s not found — service registry will be empty", _REGISTRY_DIR)
        _services = []
        return
    manifests = []
    for p in sorted(d.glob("*.json")):
        try:
            manifests.append(json.loads(p.read_text()))
        except Exception as exc:
            log.warning("Failed to parse %s: %s", p, exc)
    _services = manifests
    log.info("Loaded %d service manifests from %s", len(_services), _REGISTRY_DIR)


def all_services() -> List[dict]:
    return list(_services)


def get_service(name: str) -> dict | None:
    for svc in _services:
        if svc.get("name") == name:
            return svc
    return None
