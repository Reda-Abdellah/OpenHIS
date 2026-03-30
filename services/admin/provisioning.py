"""
User provisioning adapters.

Each adapter is idempotent: checks for existence before creating.
A failure in one adapter does not roll back Keycloak creation — it logs
the partial state. The caller is responsible for retry logic.
"""
import logging
import os
import xmlrpc.client
from typing import Optional

import httpx

log = logging.getLogger("admin.provisioning")

OPENMRS_URL  = os.environ.get("OPENMRS_URL",  "http://openmrs:8080")
OPENELIS_URL = os.environ.get("OPENELIS_URL", "http://openelis:8080")
ODOO_URL     = os.environ.get("ODOO_URL",     "http://odoo:8069")
ODOO_DB      = os.environ.get("ODOO_DB",      "odoo")
ODOO_ADMIN_PASS = os.environ.get("ODOO_ADMIN_PASS", "")


# ── OpenMRS adapter ────────────────────────────────────────────────────────────

async def _openmrs_provision(token: str, body) -> Optional[str]:
    """Create a Person + User in OpenMRS via FHIR R4. Returns openmrs UUID or None."""
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/fhir+json",
        "Accept":        "application/fhir+json",
    }
    fhir_base = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            # Check if user already exists by username identifier
            r = await c.get(
                f"{fhir_base}/Practitioner",
                params={"identifier": body.username}, headers=hdrs
            )
            if r.status_code == 200 and r.json().get("total", 0) > 0:
                existing_id = r.json()["entry"][0]["resource"]["id"]
                log.info("OpenMRS: practitioner %r already exists (%s)", body.username, existing_id)
                return existing_id

            practitioner = {
                "resourceType": "Practitioner",
                "identifier": [{"system": "openhis", "value": body.username}],
                "name": [{"family": body.last_name, "given": [body.first_name]}],
                "telecom": [{"system": "email", "value": body.email}],
                "active": True,
            }
            r = await c.post(f"{fhir_base}/Practitioner", json=practitioner, headers=hdrs)
            r.raise_for_status()
            return r.json().get("id")
    except Exception as e:
        log.warning("OpenMRS provision failed for %r: %s", body.username, e)
        return None


# ── OpenELIS adapter ───────────────────────────────────────────────────────────

async def _openelis_provision(token: str, body) -> Optional[str]:
    """Create a system user in OpenELIS via FHIR R4. Returns OpenELIS ID or None."""
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/fhir+json",
        "Accept":        "application/fhir+json",
    }
    fhir_base = f"{OPENELIS_URL}/fhir/R4"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{fhir_base}/Practitioner",
                params={"identifier": body.username}, headers=hdrs
            )
            if r.status_code == 200 and r.json().get("total", 0) > 0:
                existing_id = r.json()["entry"][0]["resource"]["id"]
                log.info("OpenELIS: practitioner %r already exists (%s)", body.username, existing_id)
                return existing_id

            practitioner = {
                "resourceType": "Practitioner",
                "identifier": [{"system": "openhis", "value": body.username}],
                "name": [{"family": body.last_name, "given": [body.first_name]}],
                "telecom": [{"system": "email", "value": body.email}],
                "active": True,
            }
            r = await c.post(f"{fhir_base}/Practitioner", json=practitioner, headers=hdrs)
            r.raise_for_status()
            return r.json().get("id")
    except Exception as e:
        log.warning("OpenELIS provision failed for %r: %s", body.username, e)
        return None


# ── Odoo adapter ───────────────────────────────────────────────────────────────

def _odoo_provision_sync(body) -> Optional[int]:
    """Create a res.users record in Odoo via XML-RPC. Returns Odoo user ID or None."""
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        uid = common.authenticate(ODOO_DB, "admin", ODOO_ADMIN_PASS, {})
        if not uid:
            log.warning("Odoo: admin authentication failed — skipping provision")
            return None

        existing = models.execute_kw(
            ODOO_DB, uid, ODOO_ADMIN_PASS,
            "res.users", "search",
            [[["login", "=", body.username]]]
        )
        if existing:
            log.info("Odoo: user %r already exists (id=%s)", body.username, existing[0])
            return existing[0]

        new_id = models.execute_kw(
            ODOO_DB, uid, ODOO_ADMIN_PASS,
            "res.users", "create",
            [{
                "name":     f"{body.first_name} {body.last_name}",
                "login":    body.username,
                "email":    body.email,
                "active":   True,
            }]
        )
        return new_id
    except Exception as e:
        log.warning("Odoo provision failed for %r: %s", body.username, e)
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

async def provision_user(keycloak_id: str, body, service_token: str) -> dict:
    """
    Provision a user across all active host applications.
    Returns a dict of system → id (or None on failure).
    """
    import asyncio
    openmrs_id  = await _openmrs_provision(service_token, body)
    openelis_id = await _openelis_provision(service_token, body)
    odoo_id     = await asyncio.to_thread(_odoo_provision_sync, body)

    results = {"openmrs": openmrs_id, "openelis": openelis_id, "odoo": odoo_id}
    failures = [k for k, v in results.items() if v is None]
    if failures:
        log.warning(
            "User %r provisioned with partial failures in: %s",
            body.username, failures
        )
    return results


async def deprovision_user(user_id: str) -> None:
    """
    Disable the user in host applications.
    Does not hard-delete — preserves audit trail.
    Keycloak must already be disabled before calling this.
    """
    # Host apps pick up the disabled state on next OIDC token validation.
    # For Odoo (XML-RPC), explicitly archive the user.
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        uid = common.authenticate(ODOO_DB, "admin", ODOO_ADMIN_PASS, {})
        if uid:
            existing = models.execute_kw(
                ODOO_DB, uid, ODOO_ADMIN_PASS,
                "res.users", "search",
                [[["active", "in", [True, False]]]]  # search archived too
            )
            if existing:
                models.execute_kw(
                    ODOO_DB, uid, ODOO_ADMIN_PASS,
                    "res.users", "write",
                    [existing, {"active": False}]
                )
    except Exception as e:
        log.warning("Odoo deprovision failed for keycloak_id=%r: %s", user_id, e)
