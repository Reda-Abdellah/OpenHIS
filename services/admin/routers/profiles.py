"""
Profile management — enable/disable deployment profiles via the admin API.

POST /api/profiles/enable   body: {"profiles": ["emr", "laboratory"]}
POST /api/profiles/disable  body: {"profiles": ["erp"]}
GET  /api/profiles/active   — current active profile list from .env

These endpoints write to .env and trigger nginx config regeneration.
They do NOT start/stop containers — that requires `make up` or `opm up`
after the profile change, which is intentional (avoid accidental reboots
through the web UI).
"""
import os
import subprocess
import sys
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from jwt_auth import require_token

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

_HERE     = os.path.dirname(__file__)
_REPO     = os.path.normpath(os.path.join(_HERE, "..", "..", "..", ".."))
_ENV_FILE = os.path.join(_REPO, ".env")
_OPM      = os.path.join(_REPO, "platform", "opm.py")

_KNOWN = {"emr", "laboratory", "erp", "imaging", "analytics"}


class ProfileList(BaseModel):
    profiles: list[str]


def _read_active() -> list[str]:
    if not os.path.exists(_ENV_FILE):
        return []
    for line in open(_ENV_FILE).read().splitlines():
        line = line.strip()
        if line.startswith("OPENHIS_PROFILES="):
            raw = line.split("=", 1)[1].strip()
            return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _write_active(profiles: list[str]) -> None:
    env_text = ""
    if os.path.exists(_ENV_FILE):
        env_text = open(_ENV_FILE).read()

    new_line = f"OPENHIS_PROFILES={','.join(profiles)}"
    lines = env_text.splitlines()
    replaced = False
    out = []
    for line in lines:
        if line.strip().startswith("OPENHIS_PROFILES="):
            out.append(new_line)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_line)

    with open(_ENV_FILE, "w") as f:
        f.write("\n".join(out) + "\n")


def _regen_nginx(active: list[str]) -> None:
    """Regenerate nginx.conf and reload nginx (best-effort)."""
    try:
        subprocess.run(
            [sys.executable, _OPM, "nginx", "--no-reload"],
            env={**os.environ, "OPENHIS_PROFILES": ",".join(active)},
            timeout=10,
            check=False,
        )
        subprocess.run(
            ["docker", "exec", "nginx", "nginx", "-s", "reload"],
            timeout=5,
            check=False,
        )
    except Exception:
        pass  # non-fatal: operator can manually reload


@router.get("/active")
def active_profiles():
    return {"profiles": _read_active()}


@router.post("/enable")
def enable_profiles(body: ProfileList, _=Depends(require_token)):
    unknown = [p for p in body.profiles if p not in _KNOWN]
    if unknown:
        raise HTTPException(400, f"Unknown profiles: {unknown}")

    active = _read_active()
    to_add = [p for p in body.profiles if p not in active]
    if not to_add:
        return {"message": "Already enabled", "active": active}

    new_active = active + to_add
    _write_active(new_active)
    _regen_nginx(new_active)

    return {
        "message": f"Enabled {to_add}. Run `make up` or `opm up` to start their containers.",
        "active":  new_active,
    }


@router.post("/disable")
def disable_profiles(body: ProfileList, _=Depends(require_token)):
    active = _read_active()
    to_remove = [p for p in body.profiles if p in active]
    if not to_remove:
        return {"message": "None of those profiles are active", "active": active}

    new_active = [p for p in active if p not in to_remove]
    _write_active(new_active)
    _regen_nginx(new_active)

    return {
        "message": f"Disabled {to_remove}. Run `make down && make up` to stop their containers.",
        "active":  new_active,
    }
