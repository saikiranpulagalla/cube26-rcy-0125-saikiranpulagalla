from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings
from recovery_manager.db import make_engine, make_session_factory, set_local_tenant


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def postgres_available(settings: Settings) -> bool:
    try:
        engine = make_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture
def runtime_factory(settings: Settings, postgres_available: bool) -> sessionmaker[Session]:
    if not postgres_available:
        pytest.skip("PostgreSQL integration environment unavailable")
    owner = create_engine(settings.migration_database_url, future=True)
    with owner.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE audit_event, work_attempt, work_intent, raw_envelope, tenant_state CASCADE"
            )
        )
    return make_session_factory(make_engine(settings))


@pytest.fixture
def alpha(settings: Settings):  # type: ignore[no-untyped-def]
    return settings.principals()["alpha-local-token"]


@pytest.fixture
def bravo(settings: Settings):  # type: ignore[no-untyped-def]
    return settings.principals()["bravo-local-token"]


@pytest.fixture
def tenant_session(runtime_factory: sessionmaker[Session]):  # type: ignore[no-untyped-def]
    def make(org_id: str):  # type: ignore[no-untyped-def]
        session = runtime_factory()
        transaction = session.begin()
        set_local_tenant(session, org_id)
        return session, transaction

    return make
