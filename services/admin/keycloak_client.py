"""
Keycloak Admin REST API client.

Uses the Admin REST API to manage users and realm roles.
Authenticates with the `openhis-platform` service account.
"""
import logging
import os
import time
from typing import Optional

import httpx

log = logging.getLogger("admin.keycloak_client")

KEYCLOAK_URL    = os.environ.get("KEYCLOAK_URL", "")
KEYCLOAK_REALM  = os.environ.get("KEYCLOAK_REALM", "openhis")
KC_ADMIN_URL    = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"
KC_TOKEN_URL    = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
CLIENT_ID       = os.environ.get("KEYCLOAK_CLIENT_ID", "openhis-platform")
CLIENT_SECRET   = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")

_token_cache: dict = {"token": None, "expires_at": 0}


async def _admin_token() -> str:
    """Obtain or refresh a Keycloak admin token via client_credentials."""
    if time.time() < _token_cache["expires_at"] - 30:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(KC_TOKEN_URL, data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        r.raise_for_status()
        data = r.json()
        _token_cache["token"]      = data["access_token"]
        _token_cache["expires_at"] = time.time() + data["expires_in"]
    return _token_cache["token"]


async def _headers() -> dict:
    token = await _admin_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def create_user(body) -> str:
    """Create a user in Keycloak. Returns the new user's ID."""
    hdrs = await _headers()
    payload = {
        "username":    body.username,
        "email":       body.email,
        "firstName":   body.first_name,
        "lastName":    body.last_name,
        "enabled":     True,
        "emailVerified": True,
        "credentials": [{"type": "password", "value": body.temporary_password, "temporary": True}],
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{KC_ADMIN_URL}/users", json=payload, headers=hdrs)
        r.raise_for_status()
        # Keycloak returns 201 with Location header containing the new user ID
        location = r.headers.get("Location", "")
        user_id = location.rstrip("/").split("/")[-1]
    return user_id


async def assign_roles(user_id: str, roles: list[str]) -> None:
    """Assign realm roles to a user."""
    hdrs = await _headers()
    async with httpx.AsyncClient(timeout=10.0) as c:
        # Fetch role representations
        role_objs = []
        for role_name in roles:
            r = await c.get(f"{KC_ADMIN_URL}/roles/{role_name}", headers=hdrs)
            if r.status_code == 200:
                role_objs.append(r.json())
            else:
                log.warning("Role %r not found in Keycloak — skipping", role_name)
        if role_objs:
            r = await c.post(
                f"{KC_ADMIN_URL}/users/{user_id}/role-mappings/realm",
                json=role_objs, headers=hdrs,
            )
            r.raise_for_status()


async def set_roles(user_id: str, roles: list[str]) -> None:
    """Replace all realm roles for a user."""
    hdrs = await _headers()
    async with httpx.AsyncClient(timeout=10.0) as c:
        # Remove existing realm roles
        r = await c.get(
            f"{KC_ADMIN_URL}/users/{user_id}/role-mappings/realm", headers=hdrs
        )
        r.raise_for_status()
        existing = r.json()
        if existing:
            dr = await c.request(
                "DELETE",
                f"{KC_ADMIN_URL}/users/{user_id}/role-mappings/realm",
                json=existing, headers=hdrs,
            )
            dr.raise_for_status()
    await assign_roles(user_id, roles)


async def disable_user(user_id: str) -> None:
    """Disable a Keycloak user (all active sessions are immediately rejected)."""
    hdrs = await _headers()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.put(
            f"{KC_ADMIN_URL}/users/{user_id}",
            json={"enabled": False}, headers=hdrs,
        )
        r.raise_for_status()
        # Invalidate all active sessions for this user
        await c.delete(
            f"{KC_ADMIN_URL}/users/{user_id}/sessions", headers=hdrs
        )


async def get_user(user_id: str) -> Optional[dict]:
    hdrs = await _headers()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{KC_ADMIN_URL}/users/{user_id}", headers=hdrs)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
