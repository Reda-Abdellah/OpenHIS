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

## What the platform already enforces (2026-06 hardening wave)

The following protections now ship enabled in the tree — the checklist
above covers what *you* still have to do per deployment:

- **MLLP is internal-only by default** — compose no longer publishes port
  2575 on the host. Re-expose it deliberately with
  `docker compose -f compose/base.yml -f compose/overrides/mllp-public.yml …`
  and apply the firewall restriction from the MLLP checklist above.
- **Redis AUTH** — set `REDIS_PASSWORD` in `.env` and every service and the
  event bus connect with it (empty keeps the open dev behaviour).
- **nginx njs RS256 gate** — the njs guard verifies RS256 JWT signatures
  against the Keycloak JWKS (not just token structure/expiry); `/orthanc/`
  is role-gated behind it and machine-to-machine FHIR routes are restricted
  to the pinned `openhis-net` subnet.
- **docker-socket-proxy** — ai-controller reaches Docker through a
  least-privilege socket proxy instead of mounting `/var/run/docker.sock`;
  pipeline containers are restricted to the `POC_ALLOWED_IMAGES` allowlist
  and run with memory/CPU/pids caps plus `no-new-privileges`.
- **`opm init` generates strong secrets** — every required secret is
  auto-generated (weak supplied passwords are rejected) and the Keycloak
  realm is rendered from `infra/keycloak/openhis-realm.json.j2` with those
  values. The rendered realm is gitignored: fresh clones must run
  `opm init` (or `opm demo-render` for throwaway dev values) before the
  first `make up`.
- **`/metrics` exposure model** — every native service serves a JWT-exempt
  Prometheus `GET /metrics`, but nginx returns 404 for it externally;
  scraping only works from inside the compose network
  (e.g. `http://mpi:8007/metrics`).
- **`DEV_MODE` guard** — `DEV_MODE=true` (JWT bypass) refuses to boot
  unless `ENV=development`, so the bypass cannot reach staging or
  production containers.

## Reporting Vulnerabilities

See [SECURITY.md](../../SECURITY.md) at the repo root and the
[contributor security policy](../guidelines_for_contributors/SECURITY.md).
