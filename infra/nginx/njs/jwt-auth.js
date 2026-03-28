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
 *   r.return(401) — missing or expired token
 *   r.return(403) — valid token but insufficient role
 *
 * The `admin` role always passes every guard.
 *
 * Cookie: openhis_token (set by the portal after Keycloak OIDC login)
 * Header: Authorization: Bearer <token> (fallback)
 *
 * No cryptographic signature verification — internal network guard only.
 */

function _token(r) {
    var cookie = r.variables['cookie_openhis_token'] || '';
    if (cookie) return cookie;
    var auth = r.headersIn['Authorization'] || '';
    return auth.startsWith('Bearer ') ? auth.slice(7) : '';
}

function _decode(token) {
    var parts = token.split('.');
    if (parts.length !== 3) return null;
    try {
        return JSON.parse(Buffer.from(parts[1], 'base64url').toString());
    } catch (_) {
        return null;
    }
}

function _guard(r, allowed) {
    var token = _token(r);
    if (!token) { r.return(401); return; }

    var payload = _decode(token);
    if (!payload) { r.return(401); return; }

    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) {
        r.return(401); return;
    }

    var roles = payload.roles || [];
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
function require_radiologist(r) { _guard(r, ['radiologist']); }
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
