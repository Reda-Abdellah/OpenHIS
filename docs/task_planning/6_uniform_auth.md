# OpenHIS — Identity Consistency Plan

> **Goal:** Keycloak is the single source of truth for all users and roles.
> Every other system derives from it. No parallel auth systems. No per-service passwords.

---

## Design Principles

1. **One identity, one truth** — A user exists exactly once, in Keycloak. Every other system derives its knowledge of that user from Keycloak, never the reverse.
2. **Roles are realm-level** — Roles are defined in Keycloak. Services receive role claims in JWT tokens and enforce them. They do not define parallel role systems.
3. **No opt-out from auth** — There is no flag that disables authentication on a production service. Dev mode is explicit and never the default.
4. **Machines use service accounts** — Service-to-service calls use Keycloak client credentials grants, not shared `USER/PASS` strings in environment variables.

---

## Phase 1 — Kill the Dual Auth System

**Priority:** Highest  
**Files affected:** `services/admin/security.py`, `services/admin/database.py`, `services/admin/routers/auth.py`, `services/admin/routers/users.py`, `.env.example`

The Admin service maintains its own `adminusers` / `adminsessions` SQLite tables and a full session auth stack in `security.py`. This is completely separate from Keycloak. The management plane must be the first service to dogfood SSO, not the last.

### Steps

1. **Wire Admin to Keycloak OIDC**
   - Add the `openhis-platform` OIDC client (already defined in `infra/keycloak/openhis-realm.json`) as the login provider for the Admin service.
   - Implement standard OIDC Authorization Code + PKCE login flow. On successful login, store the Keycloak access token in memory (not a custom session record).

2. **Replace `requireadmin` with JWT validation**
   - In every Admin router, replace the `requireadmin` dependency (which reads from `adminsessions`) with the shared `require_token()` JWT dependency already planned in `app/auth.py`.
   - Access to admin-only routes should additionally call `require_roles("admin")` (see Phase 4).

3. **Delete the legacy auth stack**
   - Remove `security.py` entirely.
   - Drop the `adminusers` and `adminsessions` tables from the Admin database schema.
   - Remove the `POST /api/auth/login` and `POST /api/auth/logout` endpoints that use session tokens — these are replaced by the Keycloak OIDC flow.

4. **Remove `REQUIRE_JWT=false`**
   - Delete this flag from all services.
   - Replace with a `DEV_MODE=true` flag that:
     - Logs a loud startup warning (`⚠️  DEV_MODE enabled — JWT validation is disabled`).
     - Calls `sys.exit(1)` if `DEV_MODE=true` and `ENV=production` are both set simultaneously.
   - Update `.env.example` to document this behaviour.

**Verification:** Log in to the Admin UI. Confirm the login redirects to Keycloak. Confirm that removing the Keycloak session (logout from Keycloak) immediately invalidates access to the Admin UI.

---

## Phase 2 — Replace Per-Service Passwords with Service Accounts

**Priority:** High  
**Files affected:** `services/integration-hub/app/`, `services/hl7/app/handlers.py`, `services/analytics/app/`, `services/ris/app/`, `.env.example`, `infra/keycloak/openhis-realm.json`

Every internal service currently authenticates to OpenMRS, OpenELIS, and Odoo with static `USER/PASS` credentials (`OPENMRS_PASS`, `OPENELIS_PASS`, `ODOO_PASS`). These bypass Keycloak entirely.

### Steps

1. **Create Keycloak service account clients**
   - Add to `infra/keycloak/openhis-realm.json` the following new OIDC clients, each with `serviceAccountsEnabled: true` and `directAccessGrantsEnabled: false`:
     - `integration-hub-sa`
     - `hl7-sa`
     - `analytics-sa`
     - `ris-sa`
   - Assign each client the minimum realm roles it needs (e.g., a new `internal-sync` role — not `admin`).

2. **Build a shared `app/token.py` utility**
   - Copy this module into each native service under `app/token.py`:
   ```python
   import os, time, httpx

   KEYCLOAK_TOKEN_URL = os.environ["KEYCLOAK_TOKEN_URL"]
   CLIENT_ID          = os.environ["KEYCLOAK_CLIENT_ID"]
   CLIENT_SECRET      = os.environ["KEYCLOAK_CLIENT_SECRET"]

   _cache = {"token": None, "expires_at": 0}

   async def get_service_token() -> str:
       if time.time() < _cache["expires_at"] - 60:
           return _cache["token"]
       async with httpx.AsyncClient() as c:
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
   ```

3. **Replace all `AUTH = (USER, PASS)` patterns**
   - In `services/hl7/app/handlers.py`, `services/integration-hub/app/worker.py`, etc., replace:
   ```python
   # BEFORE
   AUTH = (OPENMRS_USER, OPENMRS_PASS)
   async with httpx.AsyncClient(auth=AUTH) as c:
   ```
   with:
   ```python
   # AFTER
   from app.token import get_service_token
   token = await get_service_token()
   async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as c:
   ```

4. **Remove static credential env vars**
   - Delete `OPENMRS_USER`, `OPENMRS_PASS`, `OPENELIS_USER`, `OPENELIS_PASS`, `ODOO_USER`, `ODOO_PASS`, `ODOO_MASTER_PASS` from every service's `REQUIRED_ENV` list and from `.env.example`.
   - Add `KEYCLOAK_TOKEN_URL`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` as the replacement required vars.

5. **Configure host apps to accept service tokens**
   - OpenMRS: configure the OIDC authenticator to recognise tokens from `integration-hub-sa` as a trusted internal API caller.
   - OpenELIS / Odoo: equivalent OIDC trusted-client configuration.

**Verification:** Stop all services, remove all `*_PASS` vars from `.env`, restart. All sync operations in integration-hub should continue working via service tokens. Confirm in Keycloak's session list that `integration-hub-sa` sessions appear.

---

## Phase 3 — Propagate Keycloak Roles into Host Applications

**Priority:** Medium  
**Files affected:** OpenMRS OIDC module config, OpenELIS OIDC/LDAP config, `infra/odoo/setup-oidc.py`, `infra/keycloak/openhis-realm.json`

A `lab-tech` role assigned in Keycloak must make that user a lab technician inside OpenELIS automatically — no manual step inside OpenELIS.

### Steps

#### 3a. OpenMRS — OIDC role claim mapper

- In the `openhis-platform` Keycloak client, verify the `realm-roles` protocol mapper is publishing roles into the `roles` claim on the access token (already present in `openhis-realm.json`).
- In the OpenMRS OIDC authenticator module config, set:
 oidcAuthenticator.roleClaimName=roles
oidcAuthenticator.roleMapping.clinician=Clinician
oidcAuthenticator.roleMapping.admin=System Administrator

- Mapping table:
| Keycloak role | OpenMRS role |
|---|---|
| `clinician` | Clinician |
| `admin` | System Administrator |

#### 3b. OpenELIS — LDAP group mapper

- In Keycloak, add a Group Membership mapper to the `openelis-oidc` client that exposes realm roles as LDAP group memberships.
- Configure OpenELIS LDAP group sync:
| Keycloak role | OpenELIS role |
|---|---|
| `lab-tech` | Lab Technician |
| `admin` | Administrator |

#### 3c. Odoo — OAuth2 group sync

- Add a `groups` claim mapper to the `odoo-oidc` client in `openhis-realm.json`:
```json
{
  "name": "groups",
  "protocolMapper": "oidc-group-membership-mapper",
  "config": {
    "claim.name": "groups",
    "full.path": "false",
    "access.token.claim": "true"
  }
}
```
- Update `infra/odoo/setup-oidc.py` to configure group sync during bootstrap:
| Keycloak group | Odoo group |
|---|---|
| `pharmacist` | Inventory / User |
| `admin` | Administrator |

> **Constraint:** Do not rewrite OpenMRS or OpenELIS internal role models. The mapping is owned by OpenHIS configuration, not by forking the host application.

**Verification:** Create a user in Keycloak with role `lab-tech`. Log into OpenELIS via SSO. Confirm the user has Lab Technician permissions without any manual configuration inside OpenELIS.

---

## Phase 4 — Enforce Backend Role Checks on All Native Services

**Priority:** Medium  
**Files affected:** `services/*/app/auth.py`, all FastAPI routers, `services/portal/static/index.html`

The portal SPA currently filters visible cards based on `kc.tokenParsed.roles` in JavaScript. This is cosmetic — any user with a valid token can call any API directly. Role enforcement must be server-side.

### Steps

1. **Extend `app/auth.py` with `require_roles()`**
 ```python
 from fastapi import Depends, HTTPException

 def require_roles(*roles: str):
     async def check(payload: dict = Depends(require_token)):
         user_roles = payload.get("roles", [])
         if not any(r in user_roles for r in roles):
             raise HTTPException(403, "Insufficient role")
         return payload
     return check
 ```

2. **Apply role guards per service**

 | Service | Endpoint | Required roles |
 |---|---|---|
 | MPI | `POST /api/patients` | `clinician`, `admin` |
 | MPI | `GET /api/patients` | `clinician`, `radiologist`, `lab-tech`, `admin` |
 | MPI | `GET /api/crossref/*` | any authenticated |
 | RIS | `POST /api/worklist` | `radiologist`, `admin` |
 | RIS | `POST /api/reports` | `radiologist` |
 | RIS | `GET /api/worklist` | `clinician`, `radiologist`, `admin` |
 | Admin | all write routes | `admin` |
 | Admin | all read routes | `admin` |
 | Analytics | `GET /api/export` | `admin` |
 | Analytics | `GET /api/metrics` | `clinician`, `admin` |

3. **Update portal SPA**
 - Keep the client-side card filter as a UX convenience.
 - Add a comment block making clear it is cosmetic only and that backend enforcement is the actual security boundary.

**Verification:** Run V&V Scenario 4 (SSO Role-Based Access Control). Step 4.3 (readonly user gets 403) and Step 4.5 (non-admin cannot access Admin UI) must both pass.

---

## Phase 5 — Automated User Lifecycle via the Admin Plane

**Priority:** Completes the vision  
**Files affected:** `services/admin/routers/identity.py` (new), `services/admin/main.py`

Creating a user in Keycloak does not currently provision them in OpenMRS, OpenELIS, or Odoo. Operators must manually configure each system. The Admin service becomes the single place to manage platform users end-to-end.

### Steps

1. **Implement `routers/identity.py`**

 ```python
 # services/admin/routers/identity.py
 from fastapi import APIRouter, Depends, HTTPException
 from pydantic import BaseModel
 from app.auth import require_roles
 from app.keycloak_client import keycloak_admin
 from app.provisioning import provision_user, deprovision_user

 router = APIRouter(prefix="/api/identity", tags=["identity"])

 class CreateUserRequest(BaseModel):
     username: str
     email: str
     first_name: str
     last_name: str
     roles: list[str]        # Keycloak realm roles
     temporary_password: str

 @router.post("/users", dependencies=[Depends(require_roles("admin"))])
 async def create_user(body: CreateUserRequest):
     # 1. Create in Keycloak
     kc_id = await keycloak_admin.create_user(body)
     # 2. Assign realm roles
     await keycloak_admin.assign_roles(kc_id, body.roles)
     # 3. Provision in active host apps (idempotent, failures queued)
     await provision_user(kc_id, body)
     return {"id": kc_id, "status": "created"}

 @router.patch("/users/{user_id}/roles", dependencies=[Depends(require_roles("admin"))])
 async def update_roles(user_id: str, roles: list[str]):
     await keycloak_admin.set_roles(user_id, roles)
     # Host apps pick up new roles on next login via OIDC claims
     return {"status": "updated"}

 @router.delete("/users/{user_id}", dependencies=[Depends(require_roles("admin"))])
 async def deactivate_user(user_id: str):
     # Disable in Keycloak first — all tokens immediately rejected
     await keycloak_admin.disable_user(user_id)
     # Disable in host apps (does not hard-delete — preserves audit trail)
     await deprovision_user(user_id)
     return {"status": "disabled"}
 ```

2. **Implement `app/provisioning.py`** with one adapter per host application:
 - `openmrs_adapter.py` — creates an OpenMRS Person + User via FHIR R4.
 - `openelis_adapter.py` — creates an OpenELIS system user via REST.
 - `odoo_adapter.py` — creates a `res.users` record via XML-RPC.
 - Each adapter is **idempotent**: check if the user already exists before creating.
 - A failure in one adapter does not roll back Keycloak creation — it logs the partial state and queues a retry via the existing retry mechanism.

3. **Publish lifecycle events to Redis bus**
 ```python
 await bus.publish("identity.user-created", {"keycloak_id": kc_id, "roles": body.roles})
 await bus.publish("identity.user-disabled", {"keycloak_id": user_id})
 ```

4. **Register the router in `main.py`**
 ```python
 from routers import identity
 app.include_router(identity.router)
 ```

> **Important:** The identity router is a façade over the Keycloak Admin REST API — it does not maintain its own user store. Keycloak remains the single source of truth. If Keycloak is unreachable, `POST /api/identity/users` returns 503 cleanly.

**Verification:** Create a user via `POST /api/identity/users` with role `lab-tech`. Confirm the user appears in Keycloak, can log into OpenELIS with correct permissions, and appears in the MPI cross-reference registry.

---


## Out of Scope

- **No new identity provider** — Keycloak 24 is the standard. `security.py` is retired, not replaced.
- **No rewriting OpenMRS / OpenELIS internals** — role mapping is OIDC configuration only.
- **No fine-grained ABAC** — coarse RBAC per role type is the target; per-patient access rules are a future concern.
- **No multi-realm setup** — a single `openhis` realm serves all modules.