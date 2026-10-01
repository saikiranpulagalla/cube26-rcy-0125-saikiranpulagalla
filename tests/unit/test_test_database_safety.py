"""Destructive test setup must fail before opening a database connection."""

import os
import runpy
import sys
import types
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest


def test_optional_mode_never_falls_back_to_application_database() -> None:
    configure = runpy.run_path("tests/conftest.py")["settings"].__wrapped__
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(pytest.UsageError, match="TEST CONFIGURATION ERROR"):
            configure()


@pytest.mark.parametrize("missing", ["TEST_RUNTIME_DATABASE_URL", "TEST_OWNER_DATABASE_URL"])
def test_each_test_database_url_is_required(missing: str) -> None:
    configure = runpy.run_path("tests/conftest.py")["settings"].__wrapped__
    environment = {
        "TEST_RUNTIME_DATABASE_URL": "postgresql+psycopg://recovery_app@localhost/recovery_test",
        "TEST_OWNER_DATABASE_URL": "postgresql+psycopg://recovery_owner@localhost/recovery_test",
    }
    del environment[missing]
    with patch.dict(os.environ, environment, clear=True):
        with pytest.raises(pytest.UsageError, match="TEST CONFIGURATION ERROR"):
            configure()


def test_documented_fresh_shell_test_configuration_is_self_contained() -> None:
    configure = runpy.run_path("tests/conftest.py")["settings"].__wrapped__
    environment = {
        "TEST_RUNTIME_DATABASE_URL": "postgresql+psycopg://recovery_app@localhost/recovery_test",
        "TEST_OWNER_DATABASE_URL": "postgresql+psycopg://recovery_owner@localhost/recovery_test",
        "RECOVERY_WORKER_DATABASE_URL": "postgresql+psycopg://recovery_worker@localhost/recovery_test",
        "RECOVERY_REQUIRE_POSTGRES": "true",
    }
    with patch.dict(os.environ, environment, clear=True):
        settings = configure()

    assert settings.database_url == environment["TEST_RUNTIME_DATABASE_URL"]
    assert settings.migration_database_url == environment["TEST_OWNER_DATABASE_URL"]
    assert settings.worker_database_url == environment["RECOVERY_WORKER_DATABASE_URL"]


def test_quickstart_documents_each_fresh_shell_configuration_variable() -> None:
    quickstart = Path("README.md").read_text(encoding="utf-8")
    for name in (
        "RECOVERY_DATABASE_URL",
        "RECOVERY_MIGRATION_DATABASE_URL",
        "RECOVERY_WORKER_DATABASE_URL",
        "TEST_RUNTIME_DATABASE_URL",
        "TEST_OWNER_DATABASE_URL",
        "RECOVERY_REQUIRE_POSTGRES",
        "RECOVERY_BENCHMARK_DATABASE",
    ):
        assert name in quickstart


def test_documented_migration_environment_overrides_alembic_ini_in_sanitized_shell() -> None:
    captured: dict[str, object] = {}

    class FakeConfig:
        config_file_name = None
        config_ini_section = "alembic"

        def get_main_option(self, name: str) -> str:
            assert name == "sqlalchemy.url"
            return "postgresql+psycopg://fallback@localhost/recovery"

        def set_main_option(self, name: str, value: str) -> None:
            captured[name] = value

        def get_section(self, _section: str) -> dict[str, str]:
            return {}

    fake_context = types.SimpleNamespace(
        config=FakeConfig(),
        is_offline_mode=lambda: True,
        configure=lambda **_kwargs: None,
        begin_transaction=lambda: nullcontext(),
        run_migrations=lambda: None,
    )
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.context = fake_context
    environment = {
        "RECOVERY_MIGRATION_DATABASE_URL": "postgresql+psycopg://recovery_owner@localhost/recovery_test"
    }
    with patch.dict(os.environ, environment, clear=True), patch.dict(
        sys.modules, {"alembic": fake_alembic}
    ):
        runpy.run_path("alembic/env.py")

    assert captured["sqlalchemy.url"] == environment["RECOVERY_MIGRATION_DATABASE_URL"]
