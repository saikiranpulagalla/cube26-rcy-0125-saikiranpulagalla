from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings

EXPECTED_MIGRATION_HEAD = "0022_reconciliation_completeness"


def make_engine(settings: Settings, *, worker: bool = False) -> Engine:
    url = settings.worker_database_url if worker else settings.database_url
    return create_engine(url, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def set_local_tenant(session: Session, org_id: str) -> None:
    # Must be called inside every transaction that accesses tenant tables.
    if not org_id or org_id != org_id.strip():
        raise ValueError("Tenant context must be a non-empty normalized identifier")
    session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"), {"org_id": org_id}
    )


@contextmanager
def tenant_transaction(factory: sessionmaker[Session], org_id: str) -> Iterator[Session]:
    with factory() as session:
        with session.begin():
            set_local_tenant(session, org_id)
            yield session


def assert_safe_runtime_role(engine: Engine, *, required_role: str | None = None) -> None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT current_user, r.rolsuper, r.rolbypassrls "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
            .mappings()
            .one()
        )
        if row["rolsuper"] or row["rolbypassrls"]:
            raise RuntimeError("Unsafe runtime database role: superuser/BYPASSRLS is forbidden")
        owners = (
            connection.execute(
                text(
                    "SELECT DISTINCT tableowner FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename IN "
                    "('tenant_state', 'raw_envelope', 'work_intent', 'work_attempt', 'audit_event')"
                )
            )
            .scalars()
            .all()
        )
        if (current_user := str(row["current_user"])) and current_user in owners:
            raise RuntimeError("Unsafe runtime database role: protected-table owner is forbidden")
        if required_role is not None and row["current_user"] != required_role:
            raise RuntimeError("Unsafe runtime database role: unexpected role")


def assert_runtime_ready(engine: Engine, settings: Settings, *, required_role: str) -> None:
    settings.principals()
    assert_safe_runtime_role(engine, required_role=required_role)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if revision != EXPECTED_MIGRATION_HEAD:
            raise RuntimeError("Database migration is not current")
        protected = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c WHERE c.relkind = 'r' AND c.relname IN "
                "('tenant_state', 'raw_envelope', 'work_intent', 'work_attempt', 'audit_event', "
                "'source_record_version', 'financial_event', 'evidence_record', 'economic_obligation', "
                "'amount_derivation', 'settlement_allocation', 'settlement_reversal', 'claim_pursuit', "
                "'pursuit_allocation', 'evidence_assertion', 'evidence_lifecycle_event', 'policy_source_version', "
                "'recovery_assessment', 'synthetic_packet_reservation', 'current_recovery_recommendation')"
            )
        ).mappings().all()
        if len(protected) != 20 or any(not row["relrowsecurity"] or not row["relforcerowsecurity"] for row in protected):
            raise RuntimeError("Protected table RLS is incomplete")
