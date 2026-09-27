"""Destructive test setup must fail before opening a database connection."""

import os
import runpy
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
