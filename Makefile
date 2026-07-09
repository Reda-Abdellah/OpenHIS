# OpenHIS — Makefile
#
# Profile-aware deployment wrapper. OPENHIS_PROFILES controls which clinical
# modules are started. Set it in .env or pass it on the command line.
#
# Quick start:
#   make up                                  # Full stack (all profiles)
#   OPENHIS_PROFILES=imaging make up         # Imaging suite only
#   OPENHIS_PROFILES=emr,laboratory make up  # EMR + Lab only
#   make imaging-up                          # Convenience shorthand
#
# In Phase 2 the OPM CLI replaces manual compose invocation.
# Until then, this Makefile is the primary deployment interface.
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: up down build up-build test test-service test-unit test-integration e2e logs logs-service ps restart clean \
        base-up imaging-up emr-up lab-up erp-up analytics-up \
        openmrs-up openmrs-logs openmrs-seed openmrs-verify openmrs-clean \
        openelis-up openelis-logs openelis-verify openelis-clean \
        odoo-up odoo-logs odoo-verify odoo-clean \
        hub-up hub-logs hub-verify hub-clean \
        admin-up admin-logs mpi-up mpi-logs \
        phase5-migrate health

# ── Profile-aware compose command ─────────────────────────────────────────────
# Read OPENHIS_PROFILES from .env (if not already in environment).
# Shell-based parsing handles comma-separated list → multiple -f flags.

_PROFILES ?= $(shell grep -s '^OPENHIS_PROFILES=' .env | cut -d'=' -f2-)
ifeq ($(_PROFILES),)
  _PROFILES = emr,laboratory,erp,imaging,analytics
endif
OPENHIS_PROFILES ?= $(_PROFILES)

# Build -f flags for each profile
_PROFILE_FLAGS = $(shell echo "$(OPENHIS_PROFILES)" | tr ',' '\n' | xargs -I{} echo "-f compose/profiles/{}.yml")

# Full compose command: base + requested profiles
DC = docker compose -f compose/base.yml $(_PROFILE_FLAGS)

# ── Core targets ──────────────────────────────────────────────────────────────

# Rendered Keycloak realm — generated from infra/keycloak/openhis-realm.json.j2
# by `opm init` / `opm demo-render`; gitignored, so a fresh clone has no copy.
# Without it Keycloak imports nothing and every OIDC login fails.
_REALM_FILE = infra/keycloak/openhis-realm.json

# Start services for active profiles in detached mode
up:
	@test -f $(_REALM_FILE) || { \
	  echo "ERROR: $(_REALM_FILE) is missing — Keycloak has no realm to import (all logins would fail)."; \
	  echo "       Run 'python platform/opm.py init' first (or 'python platform/opm.py demo-render' for demo defaults)."; \
	  echo "ERREUR: $(_REALM_FILE) est introuvable — Keycloak n'a aucun realm à importer."; \
	  echo "        Exécutez d'abord 'python platform/opm.py init' (ou 'python platform/opm.py demo-render')."; \
	  exit 1; }
	$(DC) up -d

# Stop all services
down:
	$(DC) down

# Build (or rebuild) all service images
build:
	$(DC) build

# Build and start (combined)
up-build:
	$(DC) up -d --build

# Run the full test suite (unit + integration; e2e requires a live stack — see `make e2e`)
test:
	python -m pytest tests/unit tests/integration -v

# Run only the fast unit suite (no network, no Docker)
test-unit:
	python -m pytest tests/unit -q --tb=short

# Run only the integration suite (respx-mocked HTTP)
test-integration:
	python -m pytest tests/integration -q --tb=short

# Run the end-to-end V&V scenarios against a live stack (make up first).
# See docs/verification_and_validation/v-and-v-scenario.md and tests/e2e/README.md.
# Override Keycloak creds via KEYCLOAK_MASTER_USER / KEYCLOAK_MASTER_PASS if needed.
e2e:
	python -m pytest tests/e2e --e2e -v

# Run tests for a single service, e.g.: make test-service SVC=ris
test-service:
	python -m pytest tests/$(SVC)/ -v

# Tail logs for all services (Ctrl-C to stop)
logs:
	$(DC) logs -f

# Tail logs for one service, e.g.: make logs-service SVC=admin
logs-service:
	$(DC) logs -f $(SVC)

# Show running containers and their status
ps:
	$(DC) ps

# Show health status of all containers (healthy / unhealthy / starting)
health:
	docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E '(NAME|health)'

# Restart a single service, e.g.: make restart SVC=admin
restart:
	$(DC) restart $(SVC)

# Stop services and remove volumes (destructive — wipes all data)
clean:
	$(DC) down -v

# ── Profile convenience targets ───────────────────────────────────────────────
# Start base + one specific profile without affecting others.

base-up:
	docker compose -f compose/base.yml up -d

imaging-up:
	docker compose -f compose/base.yml -f compose/profiles/imaging.yml up -d

emr-up:
	docker compose -f compose/base.yml -f compose/profiles/emr.yml up -d

lab-up:
	docker compose -f compose/base.yml -f compose/profiles/laboratory.yml up -d

erp-up:
	docker compose -f compose/base.yml -f compose/profiles/erp.yml up -d

analytics-up:
	docker compose -f compose/base.yml -f compose/profiles/analytics.yml up -d

# ── Base service targets ──────────────────────────────────────────────────────

admin-up:
	docker compose -f compose/base.yml up -d admin

admin-logs:
	docker compose -f compose/base.yml logs -f admin

mpi-up:
	docker compose -f compose/base.yml up -d mpi

mpi-logs:
	docker compose -f compose/base.yml logs -f mpi

# ── OpenMRS targets (emr profile) ─────────────────────────────────────────────

# Start only OpenMRS (db + backend + frontend)
openmrs-up:
	docker compose -f compose/base.yml -f compose/profiles/emr.yml up -d openmrs-db openmrs openmrs-frontend

# Follow OpenMRS logs (backend has the interesting startup output)
openmrs-logs:
	docker compose -f compose/base.yml -f compose/profiles/emr.yml logs -f openmrs

# Seed OpenMRS with demo data (run after openmrs health check turns green)
openmrs-seed:
	python scripts/seed_openmrs.py

# Verify EMR acceptance criteria
openmrs-verify:
	python scripts/verify_openmrs.py

# Wipe OpenMRS data volumes (safe — does not touch other services)
openmrs-clean:
	docker compose -f compose/base.yml -f compose/profiles/emr.yml stop openmrs openmrs-frontend openmrs-db
	docker compose -f compose/base.yml -f compose/profiles/emr.yml rm -f openmrs openmrs-frontend openmrs-db
	docker volume rm -f openhis_openmrs-mysql openhis_openmrs-data

# ── OpenELIS targets (laboratory profile) ─────────────────────────────────────

# Start only OpenELIS (db + app)
openelis-up:
	docker compose -f compose/base.yml -f compose/profiles/laboratory.yml up -d openelis-db openelis

# Follow OpenELIS logs (Liquibase migrations are the interesting part on first boot)
openelis-logs:
	docker compose -f compose/base.yml -f compose/profiles/laboratory.yml logs -f openelis

# Verify Laboratory acceptance criteria
openelis-verify:
	python scripts/verify_openelis.py

# Wipe OpenELIS data volumes (safe — does not touch other services)
openelis-clean:
	docker compose -f compose/base.yml -f compose/profiles/laboratory.yml stop openelis openelis-db
	docker compose -f compose/base.yml -f compose/profiles/laboratory.yml rm -f openelis openelis-db
	docker volume rm -f openhis_openelis-pg openhis_openelis-lucene

# ── Odoo targets (erp profile) ────────────────────────────────────────────────

# Start only Odoo (db + app). First boot shows the Create Database page.
odoo-up:
	docker compose -f compose/base.yml -f compose/profiles/erp.yml up -d odoo-db odoo

# Follow Odoo logs
odoo-logs:
	docker compose -f compose/base.yml -f compose/profiles/erp.yml logs -f odoo

# Verify ERP acceptance criteria
odoo-verify:
	python scripts/verify_odoo.py

# Wipe Odoo data volumes (safe — does not touch other services)
odoo-clean:
	docker compose -f compose/base.yml -f compose/profiles/erp.yml stop odoo odoo-db
	docker compose -f compose/base.yml -f compose/profiles/erp.yml rm -f odoo odoo-db
	docker volume rm -f openhis_odoo-pg openhis_odoo-data

# ── Integration Hub targets ───────────────────────────────────────────────────

# Build and start integration-hub (base service — always available)
hub-up:
	docker compose -f compose/base.yml up -d --build integration-hub

# Follow integration-hub logs
hub-logs:
	docker compose -f compose/base.yml logs -f integration-hub

# Verify integration-hub acceptance criteria
hub-verify:
	python scripts/verify_hub.py

# Rebuild integration-hub image (after code changes)
hub-clean:
	docker compose -f compose/base.yml stop integration-hub
	docker compose -f compose/base.yml rm -f integration-hub

# ── Data migration ────────────────────────────────────────────────────────────

# Migrate data from legacy SQLite DBs to OpenMRS + OpenELIS.
# Run this BEFORE bringing down old services.
# The DB paths must be the host paths of the mounted volumes.
phase5-migrate:
	python scripts/migrate_to_openmrs.py
