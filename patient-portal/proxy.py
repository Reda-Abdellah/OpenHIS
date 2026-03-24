"""
Thin safe HTTP proxy to backend clinical services.
All functions return None on error — callers should handle gracefully.
"""
import logging, os
import httpx

log     = logging.getLogger('portal.proxy')
TIMEOUT = httpx.Timeout(8.0)


async def get(url: str):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.debug(f"GET {url} → {type(e).__name__}: {e}")
        return None


async def post(url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.debug(f"POST {url} → {type(e).__name__}: {e}")
        return None
