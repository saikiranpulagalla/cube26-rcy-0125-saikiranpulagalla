from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recovery_manager.api import create_app


def test_readiness_reports_disabled_capabilities_for_safe_runtime(runtime_factory, settings) -> None:
    app = create_app(settings=settings, factory=runtime_factory)
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert set(response.json()["capabilities"].values()) == {"DISABLED"}


def test_invalid_auth_configuration_prevents_startup(runtime_factory, settings) -> None:
    invalid = settings.model_copy(update={"dev_credentials": "{}"})
    with pytest.raises(ValueError):
        with TestClient(create_app(settings=invalid, factory=runtime_factory)):
            pass
