from __future__ import annotations

import pytest

from recovery_manager.config import Settings


def test_default_configuration_does_not_enable_demo_credentials() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        Settings(_env_file=None).principals()


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
