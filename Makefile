# Every docker interaction goes through a named compose project so nothing
# stray is left behind. Never `docker run` fixtures by hand.
COMPOSE_TEST := docker compose -p dtk-test -f docker/compose.test.yml
COMPOSE      := docker compose -p dtk -f docker/compose.yml

.PHONY: help install fmt lint type test test-unit test-integration \
        fixtures-up fixtures-down fixtures-logs up down logs clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Sync the dev environment with uv
	uv sync --all-extras

fmt:  ## Format
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint:  ## Lint
	uv run ruff check src tests
	uv run ruff format --check src tests

type:  ## Type check
	uv run mypy

fixtures-up:  ## Start test PostgreSQL + Redis (compose project: dtk-test)
	$(COMPOSE_TEST) up -d --wait

fixtures-down:  ## Stop and remove test fixtures, including volumes
	$(COMPOSE_TEST) down -v --remove-orphans

fixtures-logs:
	$(COMPOSE_TEST) logs --tail=100

test-unit:  ## Unit and replay tests, no services required
	uv run pytest tests/unit tests/replay -q

test-integration: fixtures-up  ## Integration tests against real PostgreSQL + Redis
	uv run pytest tests/integration -q -m integration; \
	  status=$$?; $(COMPOSE_TEST) down -v --remove-orphans; exit $$status

test: test-unit test-integration  ## Everything

up:  ## Start the full stack
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down --remove-orphans

logs:
	$(COMPOSE) logs -f

clean: fixtures-down  ## Remove every dtk container, network and volume
	-$(COMPOSE) down -v --remove-orphans
