from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from recovery_manager.db import assert_safe_runtime_role, make_engine, set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import AuditEvent, RawEnvelope, WorkIntent


def _accept(factory, principal, settings, key: str):
    with factory() as session, session.begin():
        set_local_tenant(session, principal.org_id)
        return accept_input(
            session, principal, b"tenant content", "application/octet-stream", "x", key, settings
        )


def test_runtime_role_is_restricted(runtime_factory, settings) -> None:
    assert_safe_runtime_role(make_engine(settings))


def test_alpha_cannot_read_bravo_envelope_or_audit(runtime_factory, alpha, bravo, settings) -> None:
    bravo_result = _accept(runtime_factory, bravo, settings, "bravo-key")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert session.get(RawEnvelope, bravo_result.envelope_id) is None
        assert session.execute(select(AuditEvent)).scalars().all() == []


def test_cross_tenant_work_reference_is_rejected(runtime_factory, alpha, bravo, settings) -> None:
    bravo_result = _accept(runtime_factory, bravo, settings, "bravo-key")
    with pytest.raises(IntegrityError):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, alpha.org_id)
            session.add(
                WorkIntent(
                    org_id=alpha.org_id, envelope_id=bravo_result.envelope_id, max_attempts=1
                )
            )
            session.flush()


def test_pool_switch_does_not_retain_previous_tenant_context(
    runtime_factory, alpha, bravo, settings
) -> None:
    alpha_result = _accept(runtime_factory, alpha, settings, "alpha-key")
    _accept(runtime_factory, bravo, settings, "bravo-key")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, bravo.org_id)
        assert session.get(RawEnvelope, alpha_result.envelope_id) is None
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert session.get(RawEnvelope, alpha_result.envelope_id) is not None


def test_missing_tenant_context_is_default_deny(runtime_factory) -> None:
    with runtime_factory() as session, session.begin():
        assert session.execute(select(RawEnvelope)).scalars().all() == []
