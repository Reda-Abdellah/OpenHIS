.PHONY: up down build test logs clean restart ps \
        openmrs-up openmrs-logs openmrs-seed openmrs-verify openmrs-clean

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
