# Adding Keycloak SSO to a Third-Party Module

This guide documents the steps and non-obvious pitfalls for integrating Keycloak
OIDC single sign-on into a third-party application running inside the OpenHIS stack.
It is based on the working integrations for OpenELIS Global 2 and Odoo 17.

---

## Overview

OpenHIS runs Keycloak at `http://localhost/keycloak/realms/openhis` (exposed via
nginx). Third-party apps authenticate against this realm using the Authorization
Code flow. The two pieces required are:

1. **A Keycloak client** in `infra/keycloak/openhis-realm.json`
2. **App-side OIDC configuration** (properties file, env vars, or config UI)

Additionally, nginx must route any OAuth2 callback URLs the app registers,
and the app must be able to reach the Keycloak discovery endpoint from inside
its container.

---

## Step 1 — Register a Keycloak client

Add a client entry to `infra/keycloak/openhis-realm.json` in the `"clients"` array:

```json
{
  "clientId": "myapp-oidc",
  "name": "My App OIDC Client",
  "enabled": true,
  "clientAuthenticatorType": "client-secret",
  "secret": "myapp-oidc-secret",
  "redirectUris": [ "http://localhost/myapp/login/oauth2/code/*" ],
  "webOrigins": [ "http://localhost" ],
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": false,
  "protocol": "openid-connect",
  "publicClient": false
}
```

**Redirect URIs must match exactly** what the app puts in the `redirect_uri`
parameter of the authorization request. Check the app's documentation or its
startup logs to find the exact callback path.

The realm JSON is imported at Keycloak startup. To update a live running stack
without restarting Keycloak, use the Admin REST API:

```bash
KC_TOKEN=$(curl -s -X POST \
  'http://localhost/keycloak/realms/master/protocol/openid-connect/token' \
  -d 'grant_type=password&client_id=admin-cli&username=admin&password=admin' \
  | jq -r '.access_token')

# Find the internal UUID for your client:
curl -s -H "Authorization: Bearer $KC_TOKEN" \
  'http://localhost/keycloak/admin/realms/openhis/clients' \
  | jq '.[] | select(.clientId=="myapp-oidc") | .id'

# Update redirectUris:
curl -s -X PUT \
  "http://localhost/keycloak/admin/realms/openhis/clients/<UUID>" \
  -H "Authorization: Bearer $KC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"redirectUris": ["http://localhost/myapp/login/oauth2/code/*"]}'
```

---

## Step 2 — Expose the callback URL via nginx

The OAuth2 callback URL (where Keycloak redirects the browser after login) must
be routable through nginx. If it shares a prefix with the app's main location
block, no extra rule is needed. If it does not, add a dedicated location.

**Example — Odoo's callback is `/auth_oauth/signin`**, which is outside the
`/odoo/` prefix, so it needs its own block:

```nginx
# infra/nginx/nginx.conf.j2 — inside the ERP profile block
location /auth_oauth/ {
    proxy_pass         http://odoo_be;
    proxy_set_header   Host               $host;
    proxy_set_header   X-Forwarded-For    $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto  $scheme;
    proxy_set_header   X-Forwarded-Host   $host;
    proxy_read_timeout 300s;
}
```

Always edit `nginx.conf.j2` and regenerate — never hand-edit `nginx.conf`.

---

## Step 3 — Make Keycloak reachable from inside the container

The app needs to fetch the OIDC discovery document from
`http://localhost/keycloak/realms/openhis/.well-known/openid-configuration`
at startup. Inside a container, `localhost` resolves to `127.0.0.1` (the
container itself), not to the host running nginx.

Fix: add `extra_hosts` to the service in its compose profile:

```yaml
# compose/profiles/<profile>.yml
services:
  myapp:
    extra_hosts:
      - "localhost:host-gateway"   # resolves to Docker host IP where nginx listens
```

The JVM has a hardcoded glibc override that always maps `localhost` → `127.0.0.1`
regardless of `/etc/hosts`. For Java apps, pass a custom hosts file to the JVM:

```sh
# In entrypoint-wrapper.sh or similar
GATEWAY_IP=$(awk '/host-gateway|localhost/ && !/^127\./ && !/^::/' /etc/hosts | awk '{print $1}' | head -1)
cat > /tmp/java-hosts <<EOF
$GATEWAY_IP localhost
EOF
# Add Docker service names too
for svc in keycloak openelis-db redis; do
    ip=$(getent hosts "$svc" | awk '{print $1}')
    [ -n "$ip" ] && echo "$ip $svc" >> /tmp/java-hosts
done
export CATALINA_OPTS="$CATALINA_OPTS -Djdk.net.hosts.file=/tmp/java-hosts"
```

---

## Step 4 — Configure the app's OIDC client

Each app has its own way to accept OIDC settings. Common patterns:

### A — Spring Security (OpenELIS-style)

OpenELIS Global 2 reads OIDC config from `/run/secrets/extra.properties`:

```properties
# infra/openelis/extra.properties
org.itech.login.oauth=true
org.itech.login.oauth.config=http://localhost/keycloak/realms/openhis
org.itech.login.oauth.clientID=openelis-oidc
org.itech.login.oauth.clientSecret=openelis-oidc-secret
```

Mount it in the compose service:

```yaml
volumes:
  - ../infra/openelis/extra.properties:/run/secrets/extra.properties:ro
```

The registration ID Spring Security derives from
`ClientRegistrations.fromOidcIssuerLocation()` defaults to the **issuer
hostname** (e.g., `localhost`). The authorization endpoint is therefore:

```
/OpenELIS-Global/oauth2/authorization/localhost
```

And the callback is:

```
/OpenELIS-Global/login/oauth2/code/localhost
```

Make sure the Keycloak `redirectUris` match the callback path exactly.

### B — Odoo (OAuth provider module)

Odoo reads its OIDC client from the database, pre-seeded by a setup script.
See `infra/odoo/setup_oidc.py` for the pattern. The important fields are
`auth_endpoint`, `token_endpoint`, `validation_endpoint` (JWKS URI), and
`client_id` / `client_secret`.

---

## Step 5 — Ensure startup ordering

If the app fetches the OIDC discovery document at startup, it must not start
before Keycloak is healthy. Add a `depends_on` condition:

```yaml
# compose/profiles/<profile>.yml
services:
  myapp:
    depends_on:
      keycloak:
        condition: service_healthy
```

Without this, on a cold boot the app may start before Keycloak's HTTP listener
is ready, fail to load the OIDC configuration, and stay broken until manually
restarted.

---

## Step 6 — Fix the redirect_uri scheme (HTTP stacks only)

In development, OpenHIS runs on plain HTTP. This creates a conflict:

- nginx receives `http://` requests and proxies them to the app.
- If nginx sends `X-Forwarded-Proto: https`, Spring Security generates
  `redirect_uri=https://localhost/...`. Keycloak redirects the browser to
  `https://localhost`, which has no TLS listener → "unable to connect".
- If nginx sends `X-Forwarded-Proto: http` (i.e., `$scheme`), the Tomcat
  `web.xml` `CONFIDENTIAL` transport-guarantee fires and redirects to
  `https://localhost:8443` → same result.

The correct fix is to **remove the CONFIDENTIAL transport-guarantee** from the
app's `web.xml` and send `X-Forwarded-Proto: $scheme` from nginx (so Spring
generates `http://` redirect URIs).

For Tomcat-based apps that bundle `web.xml` inside a WAR:

1. Extract `web.xml` from the running container:

    ```bash
    docker cp openhis-<app>-1:/usr/local/tomcat/webapps/<App>/WEB-INF/web.xml \
        infra/<app>/web.xml
    ```

2. Change `<transport-guarantee>CONFIDENTIAL</transport-guarantee>` to `NONE`.

3. Mount a Tomcat context descriptor that points to this file **before** the
   WAR is read, using `altDDName` (avoids any race with WAR extraction):

    ```xml
    <!-- infra/<app>/Catalina/localhost/<App>.xml -->
    <?xml version="1.0" encoding="UTF-8"?>
    <Context altDDName="/opt/patches/web.xml" logEffectiveWebXml="false">
        <!-- Re-declare any JNDI resources from META-INF/context.xml here.
             The external descriptor REPLACES META-INF/context.xml entirely. -->
        <Resource auth="Container"
            driverClassName="org.postgresql.Driver"
            maxTotal="20" maxIdle="10" maxWaitMillis="-1"
            name="jdbc/LimsDS"
            type="javax.sql.DataSource"
            url="${datasource.url}"
            username="${datasource.username}"
            password="${datasource.password}" />
        <CookieProcessor
            className="org.apache.tomcat.util.http.Rfc6265CookieProcessor"
            sameSiteCookies="strict" />
    </Context>
    ```

4. Mount both files in the compose service:

    ```yaml
    volumes:
      - ../infra/<app>/web.xml:/opt/patches/web.xml:ro
      - ../infra/<app>/Catalina/localhost/<App>.xml:/usr/local/tomcat/conf/Catalina/localhost/<App>.xml:ro
    ```

> **Critical gotcha — `altDDName` replaces `META-INF/context.xml`**
>
> When Tomcat finds a context descriptor at
> `conf/Catalina/localhost/<App>.xml`, it uses it *instead of* the WAR's
> embedded `META-INF/context.xml`. Any JNDI `<Resource>` definitions that
> lived in `META-INF/context.xml` (datasource, connection pool, etc.) must be
> duplicated into the external descriptor. Missing them causes
> `connect URL 'null'` / `No suitable driver` errors at startup.

---

## Step 7 — Update the portal link

The portal card in `infra/portal/index.html` should link to the app's root URL.
Spring Security and equivalent frameworks automatically redirect unauthenticated
requests to the Keycloak login page — pointing directly to an `/oauth2/authorization/`
endpoint is not necessary once the CONFIDENTIAL redirect is fixed.

```html
<a class="card" href="/myapp/" target="_blank" data-roles="...">
  ...
  <div class="card-path">/myapp/</div>
```

---

## Pitfall Reference

| Symptom | Root cause | Fix |
|---|---|---|
| Browser: "unable to connect" on portal link | `redirect_uri=https://...` generated; no TLS listener | Fix `web.xml` CONFIDENTIAL + use `X-Forwarded-Proto: $scheme` in nginx |
| Tomcat redirects to `:8443` | `CONFIDENTIAL` transport-guarantee in `web.xml` still active | Use `altDDName` to load patched `web.xml` before WAR extraction |
| `connect URL 'null'` / `No suitable driver` at startup | External context descriptor replaced `META-INF/context.xml`; JNDI resource lost | Re-declare `<Resource>` in the external descriptor |
| `Unable to resolve Configuration with Issuer` at startup | App started before Keycloak was ready | Add `depends_on: keycloak: condition: service_healthy` |
| `Invalid Client Registration with Id: keycloak` | Wrong registration ID in the authorization URL | Use the issuer hostname as the ID (e.g., `/oauth2/authorization/localhost`) |
| OAuth callback returns 404 or SPA page | Callback path not routed through nginx | Add a dedicated `location` block for the callback prefix |
| OIDC discovery fails inside container | `localhost` resolves to `127.0.0.1`, not the Docker host | Add `extra_hosts: ["localhost:host-gateway"]` + JVM hosts file override |
| WAR deployed in 87 ms (extraction skipped) | Pre-created directory under `webapps/` prevented WAR extraction | Never `mkdir` under `webapps/` before Tomcat starts; use `altDDName` instead |
| Session cookie not sent after login | `<secure>true</secure>` in `web.xml` blocks cookies over HTTP | Remove or conditionally set `<secure>` in the patched `web.xml` |

---

## Files Typically Modified Per Integration

```
infra/keycloak/openhis-realm.json          — add Keycloak client
infra/nginx/nginx.conf.j2                  — add callback location if needed
infra/<app>/extra.properties               — OIDC client ID / secret / issuer
infra/<app>/web.xml                        — CONFIDENTIAL → NONE (Java/Tomcat apps)
infra/<app>/Catalina/localhost/<App>.xml   — altDDName + JNDI resource (Tomcat apps)
compose/profiles/<profile>.yml             — extra_hosts, depends_on, volume mounts
infra/portal/index.html                    — update card href
```
