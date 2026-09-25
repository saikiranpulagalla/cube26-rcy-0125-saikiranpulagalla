from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def set_local_tenant(session: Session, org_id: str) -> None:
    # Must be called inside every transaction that accesses tenant tables.
    session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"), {"org_id": org_id}
    )


@contextmanager
def tenant_transaction(factory: sessionmaker[Session], org_id: str) -> Iterator[Session]:
    with factory() as session:
        with session.begin():
            set_local_tenant(session, org_id)
            yield session


def assert_safe_runtime_role(engine: Engine) -> None:
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
