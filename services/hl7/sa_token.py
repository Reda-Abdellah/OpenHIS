"""
Keycloak service-account token cache for the HL7 service.
"""
import os
import time

import httpx

KEYCLOAK_TOKEN_URL = os.environ["KEYCLOAK_TOKEN_URL"]
CLIENT_ID          = os.environ["KEYCLOAK_CLIENT_ID"]
CLIENT_SECRET      = os.environ["KEYCLOAK_CLIENT_SECRET"]

_cache: dict = {"token": None, "expires_at": 0}


async def get_service_token() -> str:
    if time.time() < _cache["expires_at"] - 60:
        return _cache["token"]
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(KEYCLOAK_TOKEN_URL, data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        r.raise_for_status()
        data = r.json()
        _cache["token"]      = data["access_token"]
        _cache["expires_at"] = time.time() + data["expires_in"]
    return _cache["token"]
