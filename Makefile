.PHONY: help up down build migrate seed test lint format typecheck

help:
	@echo "Usage:"
	@echo "  make up           Start all services"
	@echo "  make down         Stop all services"
	@echo "  make build        Build Docker images"
	@echo "  make migrate      Run database migrations"
	@echo "  make migrate-new  Create new migration (name=<name>)"
	@echo "  make test         Run all tests"
	@echo "  make lint         Run linter"
	@echo "  make format       Format code"
	@echo "  make typecheck    Run type checker"
	@echo "  make logs         Tail logs (service=<name>)"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f $(service)

migrate:
	docker compose exec api alembic upgrade head

migrate-new:
	docker compose exec api alembic revision --autogenerate -m "$(name)"

migrate-down:
	docker compose exec api alembic downgrade -1

migrate-history:
	docker compose exec api alembic history

test:
	docker compose exec api pytest -v

test-cov:
	docker compose exec api pytest --cov=app --cov-report=html

lint:
	ruff check app tests

format:
	black app tests

typecheck:
	mypy app

shell-db:
	docker compose exec postgres psql -U ${POSTGRES_USER:-social_intelligence} -d ${POSTGRES_DB:-social_intelligence_db}

shell-redis:
	docker compose exec redis redis-cli
