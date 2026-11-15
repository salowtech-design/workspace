.PHONY: sync lint format format-check check test test-unit test-integration test-security run migrate makemigrations-check

sync:
	uv sync --extra dev

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

check: lint format-check
	uv run python manage.py check

check-deploy:
	uv run python manage.py check --deploy --settings=config.settings.prod

makemigrations-check:
	uv run python manage.py makemigrations --check --dry-run

migrate:
	uv run python manage.py migrate --noinput

test:
	uv run pytest tests/ -v

test-unit:
	uv run pytest tests/unit/ -v

test-integration:
	uv run pytest tests/integration/ -v

test-security:
	uv run pytest tests/security/ -v

run:
	uv run python manage.py runserver 0.0.0.0:8000
