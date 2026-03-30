"""
User lifecycle management — single plane for creating, updating, and
disabling platform users across Keycloak and all host applications.

POST   /api/identity/users             — create user in Keycloak + provision in host apps
PATCH  /api/identity/users/{id}/roles  — replace realm roles
DELETE /api/identity/users/{id}        — disable user everywhere

All write routes require the `admin` realm role.
Keycloak is the source of truth; this router is a façade over the Admin REST API.
If Keycloak is unreachable, POST returns 503.
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from jwt_auth import require_roles
import keycloak_client
import provisioning

log = logging.getLogger("admin.identity")

REDIS_URL = os.environ.get("REDIS_URL", "")

router = APIRouter(prefix="/api/identity", tags=["identity"])


class CreateUserRequest(BaseModel):
    username:           str
    email:              str
    first_name:         str
    last_name:          str
    roles:              list[str]   # Keycloak realm roles
    temporary_password: str


async def _publish(event_type: str, payload: dict) -> None:
    """Best-effort Redis event publish."""
    if not REDIS_URL:
        return
    try:
        import json, redis.asyncio as aioredis, datetime
        from datetime import timezone
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.xadd("openhis:events", {
            "type":    event_type,
            "source":  "admin",
            "payload": json.dumps(payload),
            "ts":      datetime.datetime.now(timezone.utc).isoformat(),
        })
        await r.aclose()
    except Exception as e:
        log.warning("Event publish failed: %s", e)


@router.post("/users", dependencies=[Depends(require_roles("admin"))], status_code=201)
async def create_user(body: CreateUserRequest):
    """Create a user in Keycloak, assign roles, and provision in host apps."""
    try:
        kc_id = await keycloak_client.create_user(body)
    except Exception as e:
        raise HTTPException(503, f"Keycloak unavailable: {e}")

    await keycloak_client.assign_roles(kc_id, body.roles)

    # Obtain a service token to provision in host apps
    try:
        import httpx
        KC_TOKEN_URL = (
            f"{os.environ.get('KEYCLOAK_URL', '')}/realms/"
            f"{os.environ.get('KEYCLOAK_REALM', 'openhis')}/protocol/openid-connect/token"
        )
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(KC_TOKEN_URL, data={
                "grant_type":    "client_credentials",
                "client_id":     os.environ.get("KEYCLOAK_CLIENT_ID", "openhis-platform"),
                "client_secret": os.environ.get("KEYCLOAK_CLIENT_SECRET", ""),
            })
            r.raise_for_status()
            service_token = r.json()["access_token"]
    except Exception as e:
        log.warning("Could not obtain service token for provisioning: %s", e)
        service_token = ""

    provision_results = await provisioning.provision_user(kc_id, body, service_token)

    await _publish("identity.user-created", {"keycloak_id": kc_id, "roles": body.roles})

    return {"id": kc_id, "status": "created", "provisioned": provision_results}


@router.patch("/users/{user_id}/roles", dependencies=[Depends(require_roles("admin"))])
async def update_roles(user_id: str, body: dict):
    """Replace all realm roles for a user."""
    roles = body.get("roles", [])
    try:
        await keycloak_client.set_roles(user_id, roles)
    except Exception as e:
        raise HTTPException(503, f"Keycloak unavailable: {e}")
    # Host apps pick up new roles on next OIDC token refresh — no action needed.
    return {"status": "updated"}


@router.delete("/users/{user_id}", dependencies=[Depends(require_roles("admin"))])
async def deactivate_user(user_id: str):
    """Disable a user. Keycloak is disabled first so all tokens are immediately rejected."""
    try:
        await keycloak_client.disable_user(user_id)
    except Exception as e:
        raise HTTPException(503, f"Keycloak unavailable: {e}")

    await provisioning.deprovision_user(user_id)
    await _publish("identity.user-disabled", {"keycloak_id": user_id})

    return {"status": "disabled"}


@router.get("/users/{user_id}", dependencies=[Depends(require_roles("admin"))])
async def get_user(user_id: str):
    """Fetch a user from Keycloak."""
    try:
        user = await keycloak_client.get_user(user_id)
    except Exception as e:
        raise HTTPException(503, f"Keycloak unavailable: {e}")
    if not user:
        raise HTTPException(404, "User not found")
    return user
