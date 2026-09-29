from __future__ import annotations

import pytest

from recovery_manager.config import Settings


def test_default_configuration_does_not_enable_demo_credentials(monkeypatch) -> None:
    # CI may configure development credentials for a command that needs them.
    # This default-configuration assertion must test absence explicitly.
    monkeypatch.delenv("RECOVERY_DEV_CREDENTIALS", raising=False)
    monkeypatch.delenv("RECOVERY_DEVELOPMENT_MODE", raising=False)
    with pytest.raises(ValueError, match="must be an object"):
        Settings(_env_file=None).principals()


def test_intentionally_configured_development_credentials_are_accepted(monkeypatch) -> None:
    monkeypatch.setenv("RECOVERY_DEVELOPMENT_MODE", "true")
    monkeypatch.setenv(
        "RECOVERY_DEV_CREDENTIALS",
        '{"token":{"org_id":"alpha","actor_id":"actor","role":"operator"}}',
    )
    assert Settings(_env_file=None).principals()["token"].org_id == "alpha"


@pytest.mark.parametrize(
    "credentials",
    [
        '{"":{"org_id":"alpha","actor_id":"actor","role":"operator"}}',
        '{"token":{"org_id":" ","actor_id":"actor","role":"operator"}}',
        '{"token":{"org_id":"alpha","actor_id":1,"role":"operator"}}',
    ],
)
def test_blank_or_coerced_credential_identities_are_rejected(credentials: str) -> None:
    settings = Settings(development_mode=True, dev_credentials=credentials, _env_file=None)
    with pytest.raises(ValueError):
        settings.principals()
