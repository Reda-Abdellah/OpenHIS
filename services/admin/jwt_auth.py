"""
Keycloak JWT validation for the Admin service.

All requests require a valid Keycloak Bearer token.
No fallback to local session tokens.

Dev bypass: set DEV_MODE=true to skip validation (prints a loud startup
warning and exits with code 1 if ENV=production is also set).
"""
import logging
import os
import sys
import time
from typing import Optional

import httpx
from fastapi import Depends, Header, HTTPException

log = logging.getLogger("admin.jwt_auth")

KEYCLOAK_URL       = os.environ.get("KEYCLOAK_URL", "")
KEYCLOAK_REALM     = os.environ.get("KEYCLOAK_REALM", "openhis")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "openhis-platform")
DEV_MODE           = os.environ.get("DEV_MODE", "false").lower() == "true"

if DEV_MODE:
    if os.environ.get("ENV", "").lower() == "production":
        sys.exit("FATAL: DEV_MODE=true is not allowed when ENV=production")
    log.warning(
        "⚠️  DEV_MODE enabled — JWT validation is DISABLED. "
        "Never set this in production."
    )

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


async def _validate_jwt(token: str) -> dict:
    if not KEYCLOAK_URL:
        raise HTTPException(503, "Identity provider not configured (KEYCLOAK_URL missing)")

    try:
        from jose import jwt as jose_jwt
    except ImportError:
        log.error("python-jose not installed; cannot validate JWT")
        raise HTTPException(500, "JWT library unavailable")

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
    """FastAPI dependency: validates Keycloak JWT. Returns decoded claims."""
    if DEV_MODE:
        return {"preferred_username": "dev", "roles": ["admin"], "sub": "dev"}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    return await _validate_jwt(authorization[7:].strip())


def require_roles(*roles: str):
    """FastAPI dependency factory: require any of the given realm roles."""
    async def check(claims: dict = Depends(require_token)) -> dict:
        user_roles = claims.get("roles", [])
        if not any(r in user_roles for r in roles):
            raise HTTPException(403, "Insufficient role")
        return claims
    return check
