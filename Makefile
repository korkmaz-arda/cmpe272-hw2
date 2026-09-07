.PHONY: help install run lint fmt test test-integration cov docker-build docker-run clean
.DEFAULT_GOAL := help

IMAGE ?= cmpe272-hw2

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  %-18s %s\n", $$1, $$2}'

install:  ## Install the app and dev dependencies
	pip install -e ".[dev]"

run:  ## Run the service locally (reads PORT from .env / the environment)
	@test -f .env || (echo "No .env found. Copy .env.example to .env first."; exit 1)
	set -a; . ./.env; set +a; \
	uvicorn app.main:app --host 0.0.0.0 --port $$PORT --reload

lint:  ## Check formatting and lint rules
	ruff check .
	ruff format --check .

fmt:  ## Auto-format and auto-fix
	ruff format .
	ruff check --fix .

test:  ## Run unit tests with coverage (integration tests are excluded)
	pytest

test-integration:  ## Run the live GitHub integration tests (needs real credentials)
	pytest -m integration --no-cov -v

cov:  ## Write an HTML coverage report to htmlcov/
	pytest --cov-report=html
	@echo "Open htmlcov/index.html"

docker-build:  ## Build the container image
	docker build -t $(IMAGE) .

docker-run:  ## Run the container with .env and a persistent volume
	docker run --rm -p 8000:8000 --env-file .env -e DB_PATH=/data/webhooks.db \
		-v hw2-data:/data --name $(IMAGE) $(IMAGE)

clean:  ## Remove caches and coverage output
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
