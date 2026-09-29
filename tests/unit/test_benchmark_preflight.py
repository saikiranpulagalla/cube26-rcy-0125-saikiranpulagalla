from __future__ import annotations

import pytest

from recovery_manager.config import Settings
from recovery_manager.db import EXPECTED_MIGRATION_HEAD, assert_benchmark_ready


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def one(self) -> object:
        return self.value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Connection:
    def __init__(self, identity: tuple[str, str, str, int], revision: str) -> None:
        self.identity = identity
        self.revision = revision

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object) -> _Result:
        if "current_user" in str(statement):
            return _Result(self.identity)
        return _Result(self.revision)


class _Engine:
    def __init__(self, identity: tuple[str, str, str, int], revision: str) -> None:
        self.identity = identity
        self.revision = revision

    def connect(self) -> _Connection:
        return _Connection(self.identity, self.revision)

    def dispose(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://recovery_app@localhost:55432/astra_benchmark",
        worker_database_url="postgresql+psycopg://recovery_worker@localhost:55432/astra_benchmark",
        migration_database_url="postgresql+psycopg://recovery_owner@localhost:55432/astra_benchmark",
        benchmark_database=True,
        _env_file=None,
    )


def _engines(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner_role: str = "recovery_owner",
    owner_database: str = "astra_benchmark",
    owner_revision: str = EXPECTED_MIGRATION_HEAD,
) -> None:
    import recovery_manager.db as db

    def fake_create_engine(url: object, **_: object) -> _Engine:
        text_url = str(url)
        if "recovery_app" in text_url:
            identity = ("recovery_app", "astra_benchmark", "127.0.0.1", 55432)
        elif "recovery_worker" in text_url:
            identity = ("recovery_worker", "astra_benchmark", "127.0.0.1", 55432)
        else:
            identity = (owner_role, owner_database, "127.0.0.1", 55432)
        return _Engine(identity, owner_revision)

    monkeypatch.setattr(db, "create_engine", fake_create_engine)


def test_benchmark_preflight_accepts_designated_common_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _engines(monkeypatch)
    assert_benchmark_ready(_settings())


def test_benchmark_preflight_rejects_owner_role_before_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engines(monkeypatch, owner_role="recovery_app")
    with pytest.raises(RuntimeError, match="owner role"):
        assert_benchmark_ready(_settings())


def test_benchmark_preflight_rejects_wrong_endpoint_before_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engines(monkeypatch, owner_database="astra_other")
    with pytest.raises(RuntimeError, match="one database"):
        assert_benchmark_ready(_settings())


def test_benchmark_preflight_rejects_migration_mismatch_before_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engines(monkeypatch, owner_revision="other")
    with pytest.raises(RuntimeError, match="migration"):
        assert_benchmark_ready(_settings())


def test_benchmark_preflight_requires_explicit_designation(monkeypatch: pytest.MonkeyPatch) -> None:
    _engines(monkeypatch)
    with pytest.raises(RuntimeError, match="RECOVERY_BENCHMARK_DATABASE"):
        assert_benchmark_ready(_settings().model_copy(update={"benchmark_database": False}))
