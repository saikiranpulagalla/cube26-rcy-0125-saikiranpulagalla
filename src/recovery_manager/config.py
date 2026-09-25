from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class Principal:
    org_id: str
    actor_id: str
    role: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECOVERY_", case_sensitive=False)

    database_url: str = (
        "postgresql+psycopg://recovery_app@localhost:5432/recovery"
    )
    migration_database_url: str = (
        "postgresql+psycopg://recovery_owner@localhost:5432/recovery"
    )
    database_required: bool = True
    max_input_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    demo_fixtures_enabled: bool = False
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    lease_seconds: int = Field(default=30, ge=1, le=3600)
    dev_credentials: str = (
        '{"alpha-local-token":{"org_id":"org_demo_alpha","actor_id":"operator_alpha","role":"operator"},'
        '"bravo-local-token":{"org_id":"org_demo_bravo","actor_id":"operator_bravo","role":"operator"}}'
    )

    def principals(self) -> dict[str, Principal]:
        try:
            raw = json.loads(self.dev_credentials)
        except json.JSONDecodeError as exc:
            raise ValueError("RECOVERY_DEV_CREDENTIALS is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("RECOVERY_DEV_CREDENTIALS must be an object")
        result: dict[str, Principal] = {}
        for credential, item in raw.items():
            if not isinstance(credential, str) or not isinstance(item, dict):
                raise ValueError("RECOVERY_DEV_CREDENTIALS has an invalid entry")
            try:
                result[credential] = Principal(
                    org_id=str(item["org_id"]),
                    actor_id=str(item["actor_id"]),
                    role=str(item["role"]),
                )
            except KeyError as exc:
                raise ValueError("RECOVERY_DEV_CREDENTIALS entry is incomplete") from exc
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
