.PHONY: verify

verify:
	python -m alembic check
	python -m ruff check src tests alembic
	python -m mypy src
	RECOVERY_REQUIRE_POSTGRES=true python -m pytest -q
