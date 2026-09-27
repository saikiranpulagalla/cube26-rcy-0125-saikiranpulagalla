from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings
from recovery_manager.db import make_engine, make_session_factory, set_local_tenant


@pytest.fixture(scope="session")
def settings() -> Settings:
    test_runtime = os.environ.get("TEST_RUNTIME_DATABASE_URL")
    test_owner = os.environ.get("TEST_OWNER_DATABASE_URL")
    if not test_runtime or not test_owner:
        raise pytest.UsageError("TEST CONFIGURATION ERROR: explicit runtime and owner test URLs are required")
    try:
        runtime_url, owner_url = make_url(test_runtime), make_url(test_owner)
        worker_url = make_url(os.environ["RECOVERY_WORKER_DATABASE_URL"]) if os.environ.get(
            "RECOVERY_WORKER_DATABASE_URL"
        ) else runtime_url.set(username="recovery_worker", password=None)
        endpoint = (owner_url.host, owner_url.port or 5432, owner_url.database)
        for url, role in ((runtime_url, "recovery_app"), (owner_url, "recovery_owner"), (worker_url, "recovery_worker")):
            if (
                url.drivername != "postgresql+psycopg"
                or url.username != role
                or url.query
                or not url.host
                or not re.fullmatch(r"(?:astra_[a-z0-9_]+|[a-z0-9_]+_test)", url.database or "")
                or (url.host, url.port or 5432, url.database) != endpoint
            ):
                raise ValueError("isolated test database URLs must use expected roles and one endpoint")
    except (ValueError, TypeError) as exc:
        raise pytest.UsageError("TEST CONFIGURATION ERROR: invalid isolated test database URLs") from exc
    return Settings(
        database_url=test_runtime,
        migration_database_url=test_owner,
        worker_database_url=worker_url.render_as_string(hide_password=False),
        _env_file=None,
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
        database, role = connection.execute(text("SELECT current_database(), current_user")).one()
        if database != make_url(settings.migration_database_url).database or role != "recovery_owner":
            raise pytest.UsageError("TEST CONFIGURATION ERROR: cleanup connection identity mismatch")
        connection.execute(
            text(
                "TRUNCATE current_recovery_recommendation, synthetic_packet_reservation, recovery_assessment, evidence_lifecycle_event, evidence_assertion, policy_source_version, pursuit_allocation, claim_pursuit, settlement_reversal, settlement_allocation, "
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
