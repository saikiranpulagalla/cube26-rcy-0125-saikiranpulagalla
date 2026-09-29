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
    model_config = SettingsConfigDict(
        env_prefix="RECOVERY_", case_sensitive=False, env_file=".env", extra="ignore"
    )

    database_url: str = (
        "postgresql+psycopg://recovery_app:change-me@localhost:5432/recovery"
    )
    migration_database_url: str = (
        "postgresql+psycopg://recovery_owner:change-me@localhost:5432/recovery"
    )
    worker_database_url: str = (
        "postgresql+psycopg://recovery_worker:change-me@localhost:5432/recovery"
    )
    database_required: bool = True
    benchmark_database: bool = False
    max_input_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    demo_fixtures_enabled: bool = False
    development_mode: bool = False
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    lease_seconds: int = Field(default=30, ge=1, le=3600)
    dev_credentials: str = "{}"

    def principals(self) -> dict[str, Principal]:
        try:
            raw = json.loads(self.dev_credentials)
        except json.JSONDecodeError as exc:
            raise ValueError("RECOVERY_DEV_CREDENTIALS is not valid JSON") from exc
        if not isinstance(raw, dict) or not raw:
            raise ValueError("RECOVERY_DEV_CREDENTIALS must be an object")
        if not self.development_mode:
            raise ValueError("Development credentials require RECOVERY_DEVELOPMENT_MODE=true")
        result: dict[str, Principal] = {}
        for credential, item in raw.items():
            if not isinstance(credential, str) or not credential.strip() or not isinstance(item, dict):
                raise ValueError("RECOVERY_DEV_CREDENTIALS has an invalid entry")
            try:
                values = (item["org_id"], item["actor_id"], item["role"])
            except KeyError as exc:
                raise ValueError("RECOVERY_DEV_CREDENTIALS entry is incomplete") from exc
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError("RECOVERY_DEV_CREDENTIALS contains blank or invalid identity")
            result[credential] = Principal(
                org_id=values[0].strip(), actor_id=values[1].strip(), role=values[2].strip()
            )
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
