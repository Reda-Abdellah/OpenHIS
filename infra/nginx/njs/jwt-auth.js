/**
 * jwt-auth.js — nginx-njs role-based access guard
 *
 * Used via the auth_request + js_content pattern (js_access is not compiled
 * into this nginx Alpine build).  Each guard function is mapped to an internal
 * /_auth/<role> location in nginx.conf and is called as a sub-request before
 * proxying to the upstream service.
 *
 * Returns:
 *   r.return(200) — access granted, original request proceeds
 *   r.return(401) — missing, malformed, expired or BADLY SIGNED token
 *   r.return(403) — valid token but insufficient role
 *
 * The `admin` role always passes every guard.
 *
 * Cookie: openhis_token (set by the portal after Keycloak OIDC login)
 * Header: Authorization: Bearer <token> (fallback)
 *
 * Signature verification: RS256 against the Keycloak realm JWKS
 * (fetched once and cached for JWKS_TTL_MS, refreshed on unknown kid).
 * Fails CLOSED: if Keycloak is unreachable the guard returns 401.
 */

var JWKS_URL =
    (typeof process !== 'undefined' && process.env && process.env.KEYCLOAK_JWKS_URL) ||
    'http://keycloak:8080/keycloak/realms/openhis/protocol/openid-connect/certs';
var JWKS_TTL_MS = 300000; // 5 minutes

var _jwks = { keys: null, fetchedAt: 0 };

function _token(r) {
    var cookie = r.variables['cookie_openhis_token'] || '';
    if (cookie) return cookie;
    var auth = r.headersIn['Authorization'] || '';
    return auth.startsWith('Bearer ') ? auth.slice(7) : '';
}

async function _fetchJwks(r) {
    var reply = await ngx.fetch(JWKS_URL);
    if (reply.status !== 200) {
        throw new Error('JWKS fetch failed: HTTP ' + reply.status);
    }
    var body = await reply.json();
    if (!body.keys || !body.keys.length) {
        throw new Error('JWKS response has no keys');
    }
    _jwks = { keys: body.keys, fetchedAt: Date.now() };
}

async function _findKey(r, kid) {
    var stale = !_jwks.keys || (Date.now() - _jwks.fetchedAt) > JWKS_TTL_MS;
    if (stale) await _fetchJwks(r);

    function lookup() {
        for (var i = 0; _jwks.keys && i < _jwks.keys.length; i++) {
            if (_jwks.keys[i].kid === kid && _jwks.keys[i].use !== 'enc') {
                return _jwks.keys[i];
            }
        }
        return null;
    }

    var key = lookup();
    if (!key && !stale) {
        // Unknown kid on a warm cache → Keycloak may have rotated keys.
        await _fetchJwks(r);
        key = lookup();
    }
    return key;
}

async function _verify(r, token) {
    var parts = token.split('.');
    if (parts.length !== 3) return null;

    var header, payload;
    try {
        header  = JSON.parse(Buffer.from(parts[0], 'base64url').toString());
        payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
    } catch (_) {
        return null;
    }

    // Only the realm's asymmetric signing algorithm is acceptable; this
    // also blocks alg=none / HS256 confusion attacks.
    if (header.alg !== 'RS256') return null;

    var jwk = await _findKey(r, header.kid);
    if (!jwk) return null;

    var cryptoKey = await crypto.subtle.importKey(
        'jwk', jwk,
        { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
        false, ['verify']
    );
    var ok = await crypto.subtle.verify(
        'RSASSA-PKCS1-v1_5', cryptoKey,
        Buffer.from(parts[2], 'base64url'),
        Buffer.from(parts[0] + '.' + parts[1])
    );
    return ok ? payload : null;
}

function _roles(payload) {
    if (Array.isArray(payload.roles)) return payload.roles;
    if (payload.realm_access && Array.isArray(payload.realm_access.roles)) {
        return payload.realm_access.roles;
    }
    return [];
}

async function _guard(r, allowed) {
    var token = _token(r);
    if (!token) { r.return(401); return; }

    var payload;
    try {
        payload = await _verify(r, token);
    } catch (e) {
        // JWKS unreachable or crypto failure — fail closed.
        r.error('jwt-auth: verification error: ' + e.message);
        r.return(401);
        return;
    }
    if (!payload) { r.return(401); return; }

    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) {
        r.return(401); return;
    }

    var roles = _roles(payload);
    var granted = roles.indexOf('admin') >= 0;
    for (var i = 0; !granted && i < allowed.length; i++) {
        if (roles.indexOf(allowed[i]) >= 0) { granted = true; }
    }

    if (!granted) { r.return(403); return; }

    // Emit identity headers — captured by auth_request_set in nginx.conf
    // and forwarded to the upstream service as X-Remote-* proxy headers.
    r.headersOut['X-Remote-User']  = payload.preferred_username || payload.sub || '';
    r.headersOut['X-Remote-Roles'] = roles.join(',');
    r.headersOut['X-Remote-Email'] = payload.email || '';
    r.return(200);
}

function require_clinician(r)   { _guard(r, ['clinician']); }
function require_lab_tech(r)    { _guard(r, ['lab-tech', 'clinician']); }
function require_radiologist(r) { _guard(r, ['radiologist', 'clinician']); }
function require_pharmacist(r)  { _guard(r, ['pharmacist', 'clinician']); }
function require_patient(r)     { _guard(r, ['patient']); }
function require_admin(r)       { _guard(r, ['admin']); }
function require_any_auth(r)    {
    _guard(r, ['clinician', 'lab-tech', 'radiologist', 'pharmacist', 'patient']);
}

export default {
    require_clinician,
    require_lab_tech,
    require_radiologist,
    require_pharmacist,
    require_patient,
    require_admin,
    require_any_auth,
};
