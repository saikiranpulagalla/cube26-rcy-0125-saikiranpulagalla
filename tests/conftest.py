from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings
from recovery_manager.db import make_engine, make_session_factory, set_local_tenant


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "RECOVERY_DATABASE_URL", "postgresql+psycopg://recovery_app:change-me@localhost:5432/recovery"
        ),
        migration_database_url=os.environ.get(
            "RECOVERY_MIGRATION_DATABASE_URL", "postgresql+psycopg://recovery_owner:change-me@localhost:5432/recovery"
        ),
        worker_database_url=os.environ.get(
            "RECOVERY_WORKER_DATABASE_URL", "postgresql+psycopg://recovery_worker:change-me@localhost:5432/recovery"
        ),
        development_mode=True,
        dev_credentials=(
            '{"alpha-local-token":{"org_id":"org_demo_alpha","actor_id":"operator_alpha","role":"operator"},'
            '"bravo-local-token":{"org_id":"org_demo_bravo","actor_id":"operator_bravo","role":"operator"}}'
        ),
    )


@pytest.fixture(scope="session")
def postgres_available(settings: Settings) -> bool:
    try:
        engine = make_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except OperationalError:
        if os.environ.get("RECOVERY_REQUIRE_POSTGRES", "").lower() == "true":
            pytest.fail("Required PostgreSQL integration environment unavailable")
        return False


@pytest.fixture
def runtime_factory(settings: Settings, postgres_available: bool) -> sessionmaker[Session]:
    if not postgres_available:
        pytest.skip("PostgreSQL integration environment unavailable")
    owner = create_engine(settings.migration_database_url, future=True)
    with owner.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE pursuit_allocation, claim_pursuit, settlement_reversal, settlement_allocation, "
                "amount_derivation, economic_obligation, evidence_record, financial_event, source_record_version, audit_event, "
                "work_attempt, work_intent, raw_envelope, tenant_state CASCADE"
            )
        )
    return make_session_factory(make_engine(settings))


@pytest.fixture
def worker_factory(settings: Settings, postgres_available: bool) -> sessionmaker[Session]:
    if not postgres_available:
        pytest.skip("PostgreSQL integration environment unavailable")
    return make_session_factory(make_engine(settings, worker=True))


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
