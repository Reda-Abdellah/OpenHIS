"""
JWT validation middleware — canonical source for OpenHIS services.

Duplicated per-service implementations (services/mpi/jwt_auth.py,
services/hl7/jwt_auth.py, …) should be replaced with:

    from openhis_sdk import JWTMiddleware
    app.add_middleware(JWTMiddleware)

Enforcement rules:
  - Only active when KEYCLOAK_URL and REQUIRE_JWT=true are both set.
  - Health, docs, and OpenAPI paths are always exempt.
  - JWKS keys are cached in memory with a 1-hour TTL.
"""
import logging
import os
import time
from typing import Optional

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("openhis_sdk.auth")

KEYCLOAK_URL       = os.environ.get("KEYCLOAK_URL", "")
KEYCLOAK_REALM     = os.environ.get("KEYCLOAK_REALM", "openhis")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "openhis-platform")
REQUIRE_JWT        = os.environ.get("REQUIRE_JWT", "false").lower() == "true"

_SKIP_PREFIXES = ("/api/health", "/docs", "/redoc", "/openapi.json")
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
        audience=KEYCLOAK_CLIENT_ID,
        options={"verify_exp": True},
    )


class JWTMiddleware(BaseHTTPMiddleware):
    """
    Global JWT validation middleware for FastAPI apps.
    Only active when KEYCLOAK_URL and REQUIRE_JWT=true are both set.
    """

    async def dispatch(self, request: Request, call_next):
        if not (KEYCLOAK_URL and REQUIRE_JWT):
            return await call_next(request)

        if any(request.url.path.startswith(p) for p in _SKIP_PREFIXES):
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
