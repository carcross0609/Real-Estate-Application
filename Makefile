.PHONY: dev dev-down dev-logs venv-unhide api-install api-test api-lint api-migrate api-run web-install web-dev web-build seed

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
# ~/Desktop is iCloud-synced; Spotlight/iCloud housekeeping periodically re-applies the
# macOS "hidden" flag to files under .venv, and Python 3.12's site.py silently skips
# hidden .pth files — this breaks the editable `deallens` install with a confusing
# ModuleNotFoundError. .venv.nosync (symlinked as .venv) opts the venv out of iCloud sync;
# venv-unhide additionally clears any hidden flag before every invocation as a cheap,
# self-healing guard regardless of which process re-hides it.
venv-unhide:
	@[ -d services/platform/.venv.nosync ] && chflags -R nohidden services/platform/.venv.nosync 2>/dev/null || true

api-install:
	cd services/platform && python3.12 -m venv .venv.nosync && ln -sf .venv.nosync .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"

api-test: venv-unhide
	cd services/platform && .venv/bin/pytest

api-lint: venv-unhide
	cd services/platform && .venv/bin/ruff check . && .venv/bin/mypy --strict src

api-migrate: venv-unhide
	cd services/platform && .venv/bin/alembic upgrade head

api-run: venv-unhide
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
