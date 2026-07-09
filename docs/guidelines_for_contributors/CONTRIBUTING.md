# Contributing to OpenHIS

Thank you for your interest in contributing! This guide covers everything you need
to get from zero to a merged pull request.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Development Environment](#development-environment)
3. [Project Structure](#project-structure)
4. [Adding a New Service](#adding-a-new-service)
5. [Writing Tests](#writing-tests)
6. [PR Checklist](#pr-checklist)
7. [Commit Message Convention](#commit-message-convention)

---

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
Please read it before contributing.

---

## Development Environment

### Prerequisites

- Docker Compose v2.20+
- Python 3.11+
- Git

### Setup

```bash
# 1. Fork and clone
git clone https://github.com/<your-fork>/openhis.git
cd openhis

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install the shared SDK and OPM in editable mode
pip install -e libs/openhis_sdk/
pip install -e platform/

# 4. Install all service dev dependencies
pip install -r requirements-dev.txt

# 5. Copy and configure .env
cp .env.example .env
# Edit .env — change all CHANGEME_BEFORE_DEPLOY values

# 6. Start the base stack
opm enable base
make up
```

### Running Tests

```bash
# Unit tests only (fast, no Docker)
pytest tests/unit/ -v

# Integration tests (uses respx HTTP mocks, no Docker)
pytest tests/integration/ -v

# Full smoke test (requires Docker stack to be running)
pytest tests/smoke/ -v
```

---

## Project Structure

```
openhis/
├── compose/          # Docker Compose files (base + per-profile + overrides)
├── libs/
│   └── openhis_sdk/  # Shared Python library — auth, bus, logging, retry
├── services/         # Native FastAPI services
│   ├── admin/
│   ├── mpi/
│   ├── integration-hub/
│   ├── hl7/
│   ├── ris/
│   ├── analytics/
│   ├── ai-controller/
│   ├── patient-portal/
│   └── _legacy/      # FROZEN — do not extend
├── pipelines/        # AI pipeline workers
├── infra/            # Third-party service configs (nginx, keycloak, orthanc…)
├── platform/         # OPM CLI (installable via pip)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── smoke/
└── docs/
```

---

## Adding a New Service

Use the OPM scaffolding command:

```bash
opm add-service my-service --profile analytics --port 8099
```

This creates `services/my-service/` with:
- `main.py` — FastAPI app with lifespan, health endpoint, SDK wired in
- `routers/` — empty router scaffold
- `Dockerfile`
- `openhis.service.json` — service manifest
- `tests/test_my_service.py`

See [docs/explaining_the_project/adding-a-module.md](../explaining_the_project/adding-a-module.md) for the full contract.

---

## Writing Tests

### Unit tests (`tests/unit/<service>/`)

- No Docker, no real network calls
- Mock Redis with `fakeredis`, mock HTTP with `respx`
- Target: every router handler + every bus consumer `dispatch()` function
- Run in < 5 seconds total per service

### Integration tests (`tests/integration/`)

- Test cross-service flows (patient registration → sync → MPI cross-ref)
- Use `respx` mocks at the HTTP boundary (not the adapter boundary)
- No live containers needed

### Smoke tests (`tests/smoke/`)

- Require full Docker stack (`make up`)
- Check all `/api/health` endpoints return 200
- Check Redis streams exist and have correct consumer groups
- Run only on merge to `main` in CI

---

## PR Checklist

Before opening a PR, confirm:

- [ ] `pytest tests/unit/` passes with no failures
- [ ] No new copy of `jwtauth.py` or `logconfig.py` outside `libs/openhis_sdk/`
- [ ] All required env vars declared in `openhis.service.json` under `env_required`
- [ ] New env vars added to `.env.example` with a comment
- [ ] `openhis.service.json` updated if ports, paths, or bus topics changed
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] No hardcoded passwords, tokens, or connection strings

---

## Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]

[optional footer]
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `security`

Examples:
```
feat(hl7): add TLS support to MLLP listener
fix(integration-hub): replace in-memory dedup sets with Redis SADD
security(jwtauth): raise 503 instead of fail-open when KEYCLOAK_URL missing
docs(adr): add ADR-0003 MPI as identity spine
```
