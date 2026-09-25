from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from recovery_manager.api import create_app
from recovery_manager.db import assert_safe_runtime_role


def test_owner_connection_is_rejected_as_an_unsafe_runtime_role(settings) -> None:
    owner_engine = create_engine(settings.migration_database_url, future=True)
    with pytest.raises(RuntimeError, match="owner"):
        assert_safe_runtime_role(owner_engine)


def test_unknown_credential_and_fixture_http_path_are_not_available(
    runtime_factory, settings
) -> None:
    app = create_app(settings=settings, factory=runtime_factory)
    with TestClient(app) as client:
        response = client.post(
            "/v1/imports",
            content=b"x",
            headers={"X-Development-Credential": "unknown", "Idempotency-Key": "unknown"},
        )
        assert response.status_code == 401
        assert client.post("/v1/fixture-load").status_code == 404


def test_unavailable_database_fails_closed_in_runtime_guard() -> None:
    unavailable = create_engine(
        "postgresql+psycopg://recovery_app@127.0.0.1:1/recovery",
        connect_args={"connect_timeout": 1},
        future=True,
    )
    with pytest.raises(OperationalError):
        assert_safe_runtime_role(unavailable)
