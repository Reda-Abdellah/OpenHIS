"""
Keycloak JWT validation for OpenHIS native services.

Validates RS256 Bearer tokens against Keycloak's JWKS endpoint.
JWKS keys are cached in memory with a 1-hour TTL.

Usage:
    from jwt_auth import require_token

    @router.get("/protected")
    async def protected(claims: dict = Depends(require_token)):
        return {"user": claims["preferred_username"]}

If KEYCLOAK_URL is not set, validation is skipped and the dependency
returns an empty claims dict (dev/test mode).
"""
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import Header, HTTPException

log = logging.getLogger("jwt_auth")

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "openhis")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "openhis-platform")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "openhis-platform-secret")

_JWKS_CACHE: Optional[dict] = None
_JWKS_FETCHED_AT: float = 0.0
_JWKS_TTL = 3600  # 1 hour


def _jwks_url() -> str:
    return f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"


async def _get_jwks() -> dict:
    global _JWKS_CACHE, _JWKS_FETCHED_AT
    now = time.monotonic()
    if _JWKS_CACHE and (now - _JWKS_FETCHED_AT) < _JWKS_TTL:
        return _JWKS_CACHE
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(_jwks_url())
            r.raise_for_status()
            _JWKS_CACHE = r.json()
            _JWKS_FETCHED_AT = now
            return _JWKS_CACHE
    except Exception as e:
        log.warning("Failed to fetch JWKS from Keycloak: %s", e)
        return _JWKS_CACHE or {}


async def validate_jwt(token: str) -> dict:
    """Validate a JWT Bearer token. Returns decoded claims on success."""
    if not KEYCLOAK_URL:
        # Keycloak not configured — dev/test mode
        return {}

    try:
        from jose import jwt as jose_jwt, JWTError
    except ImportError:
        log.error("python-jose not installed; cannot validate JWT")
        return {}

    jwks = await _get_jwks()
    if not jwks:
        raise HTTPException(503, "Identity provider unavailable")

    try:
        claims = jose_jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=KEYCLOAK_CLIENT_ID,
            options={"verify_exp": True},
        )
        return claims
    except Exception as e:
        raise HTTPException(401, f"Invalid token: {e}",
                            headers={"WWW-Authenticate": "Bearer"})


async def require_token(authorization: str = Header(default=None)) -> dict:
    """
    FastAPI dependency: validates Keycloak JWT OR falls back to admin session.
    Use this in place of require_admin on endpoints that accept both auth flows.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:].strip()

    # Try Keycloak JWT first (if configured)
    if KEYCLOAK_URL:
        return await validate_jwt(token)

    # Fallback: admin session token
    from security import validate_admin_session
    session = validate_admin_session(token)
    if not session:
        raise HTTPException(401, "Session expired or invalid",
                            headers={"WWW-Authenticate": "Bearer"})
    return {"preferred_username": session["username"],
            "roles": [session.get("role", "admin")],
            "sub": str(session["user_id"])}
