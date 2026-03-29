# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| `main` branch | ✅ Active |
| Latest release tag | ✅ Active |
| Older releases | ❌ No patches |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**
OpenHIS processes protected health information (PHI); responsible disclosure
is critical.

### How to report

Email us with:

1. A description of the vulnerability
2. Steps to reproduce
3. The affected component(s) and version
4. Your assessment of severity (CVSS score if possible)
5. Any suggested mitigations

You will receive an acknowledgement within **48 hours** and a full response
within **7 days**.

### PHI in bug reports

If your report involves real patient data, **do not include it**.
Synthesise or anonymise all examples before sending.

## Security Assumptions

OpenHIS is designed to run **inside a trusted network perimeter** (hospital
intranet or private VPC). It is **not** designed to be exposed directly to
the public internet.

Operators are responsible for:
- Network-level access controls (firewall, VPN)
- TLS termination at the load balancer or nginx layer
- Regular patching of host OS and Docker engine
- Replacing all `CHANGEME_BEFORE_DEPLOY` defaults in `.env`

## Known Hardening Requirements (pre-production)

See [docs/security.md](docs/security.md) for the full checklist, including:
- Keycloak production mode (`start` not `start-dev`)
- MLLP TLS configuration
- Redis AOF persistence
- JWT fail-open fix
