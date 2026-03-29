# Security Hardening Guide

This document covers the steps required before running OpenHIS in a
clinical environment with real patient data.

## Pre-Production Checklist

### Credentials

- [ ] Replace ALL `CHANGEME_BEFORE_DEPLOY` values in `.env`
- [ ] Use strong, unique passwords for Postgres, Keycloak admin, and the
      OpenHIS admin user (min 20 chars, mixed case + digits + symbols)
- [ ] Rotate the `SECRET_KEY` and `JWT_SECRET` values
- [ ] Verify `.gitignore` contains `.env` and `.env.*`

### Keycloak

- [ ] Use `compose/overrides/production.yml` which sets Keycloak to `start`
      mode instead of `start-dev`
- [ ] Configure `KC_HTTPS_CERTIFICATE_FILE` and `KC_HTTPS_CERTIFICATE_KEY_FILE`
- [ ] Set `KC_DB` to `postgres` (not the default H2 file store)
- [ ] Enable `ssl-required: all` in the realm settings

### MLLP / HL7

- [ ] Set `MLLP_TLS_CERT` and `MLLP_TLS_KEY` to valid certificate paths
- [ ] Configure `MLLP_MAX_MSG_BYTES` (default: 1048576 — 1 MB)
- [ ] Restrict port 2575 to known HL7 sender IPs at the firewall level

### Redis

- [ ] Ensure `infra/redis/redis.conf` has `appendonly yes`
- [ ] Set `requirepass` in `redis.conf` and update `REDIS_URL` in `.env`
- [ ] Do not expose Redis port 6379 outside the Docker network

### TLS / Network

- [ ] Configure TLS termination at the nginx layer using valid certificates
- [ ] Restrict the Docker network to the hospital intranet / VPN
- [ ] Do not expose Keycloak admin console (port 9000) externally
- [ ] Enable Postgres `ssl = on` and provide `sslcert`/`sslkey`

### Audit

- [ ] Verify `openhis.audit` Redis stream is being written to
- [ ] Configure `AUDIT_RETENTION_DAYS` (regulatory default: 6 years HIPAA,
      varies EU)
- [ ] Confirm Admin UI audit log view shows events from all services

## Reporting Vulnerabilities

See [SECURITY.md](../SECURITY.md) at the repo root.
