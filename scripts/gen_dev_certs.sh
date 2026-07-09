#!/usr/bin/env bash
# ── gen_dev_certs.sh ──────────────────────────────────────────────────────────
# Generates a self-signed TLS certificate + key for local development of the
# production override (compose/overrides/production.yml mounts them into
# Keycloak as /opt/keycloak/conf/tls.{crt,key}).
#
# Output: infra/ssl/tls.crt and infra/ssl/tls.key (gitignored).
# Valid 365 days, CN=localhost, SAN: localhost + openhis.local.
#
# ⚠️  Self-signed certs are for DEV ONLY — use CA-issued certificates in
#     production.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSL_DIR="${REPO_ROOT}/infra/ssl"

mkdir -p "${SSL_DIR}"

openssl req -x509 -newkey rsa:2048 -nodes \
    -days 365 \
    -keyout "${SSL_DIR}/tls.key" \
    -out "${SSL_DIR}/tls.crt" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:openhis.local"

chmod 600 "${SSL_DIR}/tls.key"

echo "Generated:"
echo "  ${SSL_DIR}/tls.crt"
echo "  ${SSL_DIR}/tls.key (mode 600)"
echo "Valid for 365 days — CN=localhost, SAN: localhost, openhis.local"
