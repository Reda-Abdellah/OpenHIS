.PHONY: up down build test logs clean restart ps \
        openmrs-up openmrs-logs openmrs-seed openmrs-verify openmrs-clean \
        openelis-up openelis-logs openelis-verify openelis-clean \
        odoo-up odoo-logs odoo-verify odoo-clean \
        hub-up hub-logs hub-verify hub-clean \
        phase5-migrate

# Start all services in detached mode
up:
	docker compose up -d

# Stop all services
down:
	docker compose down

# Build (or rebuild) all service images
build:
	docker compose build

# Build and start (combined)
up-build:
	docker compose up -d --build

# Run the full test suite
test:
	python -m pytest tests/ -v

# Run tests for a single service, e.g.: make test-service SVC=ehr
test-service:
	python -m pytest tests/$(SVC)/ -v

# Tail logs for all services (Ctrl-C to stop)
logs:
	docker compose logs -f

# Tail logs for one service, e.g.: make logs-service SVC=ehr
logs-service:
	docker compose logs -f $(SVC)

# Show running containers and their status
ps:
	docker compose ps

# Restart a single service, e.g.: make restart SVC=ehr
restart:
	docker compose restart $(SVC)

# Stop services and remove volumes (destructive — wipes all data)
clean:
	docker compose down -v

# Start with optional FHIR server profile
up-fhir:
	docker compose --profile fhir up -d

# ── Phase 1: OpenMRS ────────────────────────────────────────────────────────

# Start only OpenMRS (db + backend + frontend), leave other services alone
openmrs-up:
	docker compose up -d openmrs-db openmrs openmrs-frontend

# Follow OpenMRS logs (backend is the one with the interesting startup output)
openmrs-logs:
	docker compose logs -f openmrs

# Seed OpenMRS with demo data (run after openmrs health check turns green)
openmrs-seed:
	python scripts/seed_openmrs.py

# Verify Phase 1 acceptance criteria
openmrs-verify:
	python scripts/verify_openmrs.py

# Wipe OpenMRS data volumes (safe — does not touch other services)
openmrs-clean:
	docker compose stop openmrs openmrs-frontend openmrs-db
	docker compose rm -f openmrs openmrs-frontend openmrs-db
	docker volume rm -f openhis_openmrs-mysql openhis_openmrs-data

# ── Phase 2: OpenELIS ────────────────────────────────────────────────────────

# Start only OpenELIS (db + app), leave other services alone
openelis-up:
	docker compose up -d openelis-db openelis

# Follow OpenELIS logs (Liquibase migrations are the interesting part on first boot)
openelis-logs:
	docker compose logs -f openelis

# Verify Phase 2 acceptance criteria
openelis-verify:
	python scripts/verify_openelis.py

# Wipe OpenELIS data volumes (safe — does not touch other services)
openelis-clean:
	docker compose stop openelis openelis-db
	docker compose rm -f openelis openelis-db
	docker volume rm -f openhis_openelis-pg openhis_openelis-lucene

# ── Phase 3: Odoo ────────────────────────────────────────────────────────────

# Start only Odoo (db + app). First boot shows the Create Database page.
odoo-up:
	docker compose up -d odoo-db odoo

# Follow Odoo logs
odoo-logs:
	docker compose logs -f odoo

# Verify Phase 3 acceptance criteria
odoo-verify:
	python scripts/verify_odoo.py

# Wipe Odoo data volumes (safe — does not touch other services)
odoo-clean:
	docker compose stop odoo odoo-db
	docker compose rm -f odoo odoo-db
	docker volume rm -f openhis_odoo-pg openhis_odoo-data

# ── Phase 4: Integration Hub ─────────────────────────────────────────────────

# Build and start integration-hub (requires OpenMRS, OpenELIS, Odoo to be up)
hub-up:
	docker compose up -d --build integration-hub

# Follow integration-hub logs
hub-logs:
	docker compose logs -f integration-hub

# Verify Phase 4 acceptance criteria
hub-verify:
	python scripts/verify_hub.py

# Rebuild integration-hub image (after code changes)
hub-clean:
	docker compose stop integration-hub
	docker compose rm -f integration-hub

# ── Phase 5: Full Cutover ────────────────────────────────────────────────────

# Migrate data from legacy SQLite DBs to OpenMRS + OpenELIS.
# Run this BEFORE bringing down old services.
# The DB paths must be the host paths of the mounted volumes.
phase5-migrate:
	python scripts/migrate_to_openmrs.py
