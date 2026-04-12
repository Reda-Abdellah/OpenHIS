"""
JWT validation middleware and FastAPI dependencies — canonical source for OpenHIS services.

Usage (middleware — validates all non-exempt requests):
    from openhis_sdk import JWTMiddleware
    app.add_middleware(JWTMiddleware)

Usage (per-endpoint dependency):
    from openhis_sdk.auth import require_token, require_roles

    @router.get("/protected")
    async def protected(claims: dict = Depends(require_token)):
        return {"user": claims["preferred_username"]}

    @router.post("/admin-only", dependencies=[Depends(require_roles("admin"))])
    async def admin_only(): ...

Enforcement rules:
  - JWT validation is ON by default when KEYCLOAK_URL is set.
  - Set DEV_MODE=true to disable validation (logs a loud warning).
  - Services exit with code 1 if DEV_MODE=true and ENV=production.
  - Health, docs, and OpenAPI paths are always exempt from the middleware.
  - JWKS keys are cached in memory with a 1-hour TTL.
"""
import logging
import os
import sys
import time
from typing import Optional

import httpx
from fastapi import Depends, Header, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("openhis_sdk.auth")

KEYCLOAK_URL       = os.environ.get("KEYCLOAK_URL", "")
KEYCLOAK_REALM     = os.environ.get("KEYCLOAK_REALM", "openhis")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "openhis-platform")
# KEYCLOAK_AUDIENCE: expected JWT audience. Defaults to KEYCLOAK_CLIENT_ID.
# Set explicitly when the service account client ID differs from the JWT audience
# (e.g. RIS uses client_id=ris-sa but tokens carry audience=openhis-platform).
KEYCLOAK_AUDIENCE  = os.environ.get("KEYCLOAK_AUDIENCE", KEYCLOAK_CLIENT_ID)
DEV_MODE           = os.environ.get("DEV_MODE", "false").lower() == "true"

if DEV_MODE:
    if os.environ.get("ENV", "").lower() == "production":
        sys.exit("FATAL: DEV_MODE=true is not allowed when ENV=production")
    log.warning(
        "⚠️  DEV_MODE enabled — JWT validation is DISABLED. "
        "Never set this in production."
    )

_SKIP_PREFIXES = ("/api/health", "/api/auth", "/docs", "/redoc", "/openapi.json")
_JWKS_CACHE: Optional[dict] = None
_JWKS_FETCHED_AT: float = 0.0
_JWKS_TTL = 3600


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
        log.warning("Failed to fetch JWKS: %s", e)
        return _JWKS_CACHE or {}


async def validate_token(token: str) -> dict:
    """Validate a JWT against the Keycloak JWKS endpoint. Returns decoded claims."""
    try:
        from jose import jwt as jose_jwt
    except ImportError:
        log.error("python-jose not installed; cannot validate JWT")
        return {}

    jwks = await _get_jwks()
    if not jwks:
        raise ValueError("Identity provider unavailable")

    return jose_jwt.decode(
        token,
        jwks,
        algorithms=["RS256"],
        audience=KEYCLOAK_AUDIENCE,
        options={"verify_exp": True},
    )


async def require_token(authorization: str = Header(default=None)) -> dict:
    """FastAPI dependency: validates Keycloak JWT. Returns decoded claims."""
    if DEV_MODE:
        return {"preferred_username": "dev", "roles": ["admin"], "sub": "dev"}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401, "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not KEYCLOAK_URL:
        raise HTTPException(503, "Identity provider not configured (KEYCLOAK_URL missing)")

    try:
        claims = await validate_token(authorization[7:].strip())
        return claims
    except Exception as exc:
        raise HTTPException(
            401, f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_roles(*roles: str):
    """FastAPI dependency factory: require any of the given realm roles."""
    async def check(claims: dict = Depends(require_token)) -> dict:
        user_roles = claims.get("roles", [])
        if not any(r in user_roles for r in roles):
            raise HTTPException(403, "Insufficient role")
        return claims
    return check


class JWTMiddleware(BaseHTTPMiddleware):
    """
    Global JWT validation middleware for FastAPI apps.
    Active when KEYCLOAK_URL is set and DEV_MODE is not true.

    Pass extra_public_prefixes to exempt additional paths (e.g. SPA HTML root).
    """

    def __init__(self, app, extra_public_prefixes: tuple = ()):
        super().__init__(app)
        self._skip = _SKIP_PREFIXES + extra_public_prefixes

    async def dispatch(self, request: Request, call_next):
        if DEV_MODE or not KEYCLOAK_URL:
            return await call_next(request)

        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in self._skip):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            claims = await validate_token(auth[7:].strip())
            request.state.jwt_claims = claims
        except Exception as exc:
            return JSONResponse(
                {"detail": f"Invalid token: {exc}"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
