from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input


def test_runtime_can_use_context_derived_lock_but_cannot_rewrite_revision(
    runtime_factory, alpha, settings
) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        accept_input(session, alpha, b"lock", "application/octet-stream", "lock", "lock", settings)
        revision = session.execute(text("SELECT public.lock_current_tenant_revision()")).scalar_one()
        assert isinstance(revision, int)
    for value in (0, 1, -1, 1_000_000):
        with runtime_factory() as session, pytest.raises(DBAPIError), session.begin():
            set_local_tenant(session, alpha.org_id)
            session.execute(
                text("UPDATE tenant_state SET decision_revision = :value WHERE org_id = :org"),
                {"value": value, "org": alpha.org_id},
            )


def test_tenant_lock_rejects_missing_or_blank_context(runtime_factory) -> None:
    with runtime_factory() as session, pytest.raises(DBAPIError), session.begin():
        session.execute(text("SELECT public.lock_current_tenant_revision()"))
    with runtime_factory() as session, pytest.raises(DBAPIError), session.begin():
        session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        session.execute(text("SELECT public.lock_current_tenant_revision()"))
