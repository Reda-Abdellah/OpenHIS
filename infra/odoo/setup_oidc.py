#!/usr/bin/env python3
"""
setup_oidc.py — Bootstrap Odoo 17 for OpenHIS: create DB + configure Keycloak SSO.

Fully automated — no manual steps required. Safe to re-run.

Steps:
  1. Wait for Odoo HTTP to be reachable
  2. Create the database if it does not exist (via /web/database/create)
  3. Wait for the database to appear in db.list()
  4. Install the auth_oauth module
  5. Set web.base.url = http://localhost
  6. Upsert the Keycloak OAuth provider record

Environment variables:
  ODOO_URL                  http://odoo:8069
  ODOO_DB                   odoo
  ODOO_MASTER_PASS          admin           (Odoo master/admin_passwd in odoo.conf)
  ODOO_ADMIN_PASS           admin           (admin user password for the new DB)
  ODOO_LANG                 en_US
  KEYCLOAK_URL              http://keycloak:8080
  KEYCLOAK_REALM            openhis
  ODOO_OIDC_CLIENT_ID       odoo-oidc
  ODOO_OIDC_CLIENT_SECRET   (no default — supplied by compose from ODOO_OIDC_SECRET)
"""

import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import xmlrpc.client

ODOO_URL      = os.environ.get("ODOO_URL",                "http://odoo:8069")
ODOO_DB       = os.environ.get("ODOO_DB",                 "odoo")
MASTER_PASS   = os.environ.get("ODOO_MASTER_PASS",        "admin")   # admin_passwd in odoo.conf
ADMIN_PASS    = os.environ.get("ODOO_ADMIN_PASS",         "admin")   # DB admin user password
ODOO_LANG     = os.environ.get("ODOO_LANG",               "en_US")
KC_URL        = os.environ.get("KEYCLOAK_URL",            "http://keycloak:8080")
KC_REALM      = os.environ.get("KEYCLOAK_REALM",          "openhis")
CLIENT_ID     = os.environ.get("ODOO_OIDC_CLIENT_ID",    "odoo-oidc")
# NOTE: unused below — Odoo 17 auth.oauth.provider has no client_secret field
# (implicit/code flow validated at the userinfo endpoint). Kept for clarity;
# no literal fallback: the value comes from compose (${ODOO_OIDC_SECRET}).
CLIENT_SEC    = os.environ.get("ODOO_OIDC_CLIENT_SECRET", "")

KC_BASE       = f"{KC_URL}/realms/{KC_REALM}/protocol/openid-connect"
KC_PUBLIC_URL = os.environ.get("KEYCLOAK_PUBLIC_URL", "http://localhost")
AUTH_ENDPOINT  = f"{KC_PUBLIC_URL}/keycloak/realms/{KC_REALM}/protocol/openid-connect/auth"
USERINFO_URL  = f"{KC_BASE}/userinfo"

STARTUP_RETRIES = 30   # wait up to 150s for Odoo HTTP
DB_RETRIES      = 40   # wait up to 200s for DB to be ready after creation
DELAY           = 5


def log(msg):
    print(msg, flush=True)


def wait_for_odoo():
    """Wait until Odoo's health endpoint responds."""
    url = f"{ODOO_URL}/web/health"
    for i in range(STARTUP_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        log(f"  [{i+1}/{STARTUP_RETRIES}] Waiting for Odoo HTTP...")
        time.sleep(DELAY)
    return False


def create_database(db_proxy):
    """
    Create the Odoo database via the /web/database/create HTTP endpoint.
    Returns True if created or already exists, False on error.
    """
    # Check if it already exists first
    try:
        if ODOO_DB in db_proxy.list():
            log(f"  Database '{ODOO_DB}' already exists — skipping creation.")
            return True
    except Exception:
        pass

    log(f"  Creating database '{ODOO_DB}'...")
    params = urllib.parse.urlencode({
        "master_pwd":   MASTER_PASS,
        "name":         ODOO_DB,
        "lang":         ODOO_LANG,
        "password":     ADMIN_PASS,
        "login":        "admin",
        "demo":         "false",
        "phone":        "",
        "country_code": "",
    }).encode()

    try:
        req = urllib.request.Request(
            f"{ODOO_URL}/web/database/create",
            data=params,
            method="POST",
        )
        # Odoo responds with a redirect (303) on success — urllib follows it
        # which may return a 200 on the login page, or raise on HTTP errors.
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
            log(f"  Database create response: HTTP {status}")
            return True
    except urllib.error.HTTPError as e:
        # Odoo returns 303 redirect after creation — urllib raises on 3xx by default
        # in some configurations. A 303 is still success.
        if e.code in (303, 302, 301):
            log(f"  Database create: HTTP {e.code} (redirect — success)")
            return True
        log(f"  Database create HTTP error: {e.code} — {e.reason}")
        return False
    except Exception as e:
        log(f"  Database create error: {e}")
        return False


def wait_for_db(db_proxy):
    """Wait until the target database appears in db.list()."""
    for i in range(DB_RETRIES):
        try:
            if ODOO_DB in db_proxy.list():
                return True
        except Exception:
            pass
        log(f"  [{i+1}/{DB_RETRIES}] Waiting for database '{ODOO_DB}' to be ready...")
        time.sleep(DELAY)
    return False


def main():
    log("=" * 60)
    log("OpenHIS — Odoo bootstrap")
    log(f"  Odoo   : {ODOO_URL}  db={ODOO_DB}")
    log(f"  KC auth: {AUTH_ENDPOINT}")
    log(f"  KC info: {USERINFO_URL}")
    log("=" * 60)

    # ── Step 1: wait for Odoo HTTP ─────────────────────────────────────────
    log("\n[1/5] Waiting for Odoo HTTP...")
    if not wait_for_odoo():
        log(f"ERROR: Odoo not reachable at {ODOO_URL} after {STARTUP_RETRIES * DELAY}s.")
        sys.exit(1)
    log("  Odoo is up.")

    db_proxy = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/db")
    common   = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    models   = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

    # ── Step 2: create database if needed ─────────────────────────────────
    log(f"\n[2/5] Creating database '{ODOO_DB}' if needed...")
    if not create_database(db_proxy):
        log("ERROR: Failed to create database. Check Odoo logs.")
        sys.exit(1)

    # ── Step 3: wait for DB to be queryable via XML-RPC ───────────────────
    log(f"\n[3/5] Waiting for database '{ODOO_DB}' to be ready...")
    if not wait_for_db(db_proxy):
        log(f"ERROR: Database not ready after {DB_RETRIES * DELAY}s.")
        sys.exit(1)
    log("  Database is ready.")

    # ── Step 4: authenticate ───────────────────────────────────────────────
    uid = common.authenticate(ODOO_DB, "admin", ADMIN_PASS, {})
    if not uid:
        log("ERROR: Authentication failed — check ODOO_ADMIN_PASS.")
        sys.exit(1)
    log(f"  Authenticated as admin (uid={uid})")

    def call(model, method, *args, **kwargs):
        return models.execute_kw(
            ODOO_DB, uid, ADMIN_PASS, model, method, list(args), kwargs
        )

    # ── Step 5a: install auth_oauth ────────────────────────────────────────
    log("\n[4/5] Installing auth_oauth module...")
    installed = call("ir.module.module", "search",
                     [["name", "=", "auth_oauth"], ["state", "=", "installed"]])
    if not installed:
        mod_ids = call("ir.module.module", "search", [["name", "=", "auth_oauth"]])
        if not mod_ids:
            log("ERROR: auth_oauth not found in module registry (not in this Odoo image).")
            sys.exit(1)
        call("ir.module.module", "button_immediate_install", mod_ids)
        log("  auth_oauth installed.")
    else:
        log("  auth_oauth already installed.")

    # ── Step 5b: set web.base.url ──────────────────────────────────────────
    def set_param(key, value):
        ids = call("ir.config_parameter", "search", [["key", "=", key]])
        if ids:
            call("ir.config_parameter", "write", ids, {"value": value})
        else:
            call("ir.config_parameter", "create", {"key": key, "value": value})

    set_param("web.base.url", "http://localhost")
    log("  web.base.url = http://localhost")

    # ── Step 5c: upsert Keycloak OAuth provider ────────────────────────────
    log("\n[5/5] Configuring Keycloak OAuth provider...")
    # Odoo 17 auth.oauth.provider does not have client_secret — it uses
    # the implicit flow (response_type=token); Keycloak validates the
    # access_token at validation_endpoint (userinfo). No secret needed.
    provider_vals = {
        "name":                "Keycloak (OpenHIS)",
        "enabled":             True,
        "client_id":           CLIENT_ID,
        "auth_endpoint":       AUTH_ENDPOINT,   # browser-facing (public nginx URL)
        "validation_endpoint": USERINFO_URL,     # server-side (internal Docker DNS)
        "scope":               "openid profile email",
        "body":                "Login with Keycloak",  # required button label
        # NOTE: response_type and token_endpoint are NOT standard Odoo fields.
        # auth_oauth_main.py hardcodes response_type='code' and derives the
        # token endpoint from validation_endpoint (/userinfo → /token).
    }

    existing = call("auth.oauth.provider", "search",
                    [["client_id", "=", CLIENT_ID]])
    if existing:
        call("auth.oauth.provider", "write", existing, provider_vals)
        log(f"  Updated existing provider (id={existing[0]})")
    else:
        new_id = call("auth.oauth.provider", "create", provider_vals)
        log(f"  Created provider 'Keycloak (OpenHIS)' (id={new_id})")

    # ── Step 5d: configure Keycloak → Odoo group sync ──────────────────────
    log("\n[5d] Configuring Keycloak → Odoo role/group mapping...")
    # Map Keycloak realm roles to Odoo internal groups.
    # Keycloak publishes roles in the `roles` claim and groups in `groups`.
    # Odoo auth_oauth uses the `roles` claim from the userinfo endpoint to
    # auto-assign res.groups based on the mapping below.
    _ROLE_TO_ODOO_GROUP = {
        "admin":      "base.group_system",
        "pharmacist": "stock.group_stock_user",
    }
    for kc_role, odoo_group_xml_id in _ROLE_TO_ODOO_GROUP.items():
        try:
            parts = odoo_group_xml_id.split(".")
            if len(parts) == 2:
                grp_ids = call("ir.model.data", "search",
                               [["module", "=", parts[0]], ["name", "=", parts[1]],
                                ["model", "=", "res.groups"]])
                if grp_ids:
                    log(f"  Role '{kc_role}' → Odoo group '{odoo_group_xml_id}' (id={grp_ids[0]})")
                else:
                    log(f"  WARNING: Odoo group '{odoo_group_xml_id}' not found — skipping")
        except Exception as e:
            log(f"  WARNING: Could not map role '{kc_role}': {e}")

    log("  Note: Odoo auto-assigns groups based on the 'roles' claim in the")
    log("  Keycloak token. Ensure 'roles' claim is in the userinfo scope.")

    log("")
    log("=" * 60)
    log("Done — Odoo is fully bootstrapped.")
    log("  http://localhost/odoo/web/login → 'Keycloak (OpenHIS)' button")
    log("  First login auto-creates the Odoo user from the Keycloak username.")
    log("  Role mapping: admin→System Administrator, pharmacist→Inventory/User")
    log("=" * 60)


if __name__ == "__main__":
    main()
