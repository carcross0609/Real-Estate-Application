.PHONY: dev dev-down dev-logs api-install api-test api-lint api-migrate api-run web-install web-dev web-build seed

COMPOSE = docker compose -f infra/docker/docker-compose.dev.yml

## --- Local infra (Postgres/PostGIS, Redis, MinIO) ---
dev:
	$(COMPOSE) up -d
	@echo "Postgres :5432  Redis :6379  MinIO :9000 (console :9001)"

dev-down:
	$(COMPOSE) down

dev-logs:
	$(COMPOSE) logs -f

## --- Backend (services/platform) ---
api-install:
	cd services/platform && python3.12 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"

api-test:
	cd services/platform && .venv/bin/pytest

api-lint:
	cd services/platform && .venv/bin/ruff check . && .venv/bin/mypy --strict src

api-migrate:
	cd services/platform && .venv/bin/alembic upgrade head

api-run:
	cd services/platform && .venv/bin/uvicorn deallens.api:app --reload --port 8000

## --- Frontend (apps/web) ---
web-install:
	cd apps/web && npm install

web-dev:
	cd apps/web && npm run dev

web-build:
	cd apps/web && npm run build

## --- One-command bootstrap ---
seed: dev api-migrate
	@echo "Dev environment up and migrated."
