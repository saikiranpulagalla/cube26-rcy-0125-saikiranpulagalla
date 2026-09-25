from __future__ import annotations

from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import IdempotencyConflict, accept_input
from recovery_manager.models import AuditEvent, RawEnvelope, TenantState, WorkIntent


def _accept(
    factory: sessionmaker[Session], principal, settings, raw: bytes = b"raw input", key: str = "k-1"
):
    with factory() as session, session.begin():
        set_local_tenant(session, principal.org_id)
        return accept_input(
            session, principal, raw, "text/csv" if raw.startswith(b"org_id,") else "application/octet-stream", "test.bin", key, settings
        )


def test_acceptance_atomically_creates_raw_work_audit_and_revision(
    runtime_factory, alpha, settings
) -> None:
    raw = b"exact \x00 bytes\n"
    result = _accept(runtime_factory, alpha, settings, raw)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        envelope = session.get(RawEnvelope, result.envelope_id)
        assert envelope is not None
        assert envelope.raw_bytes == raw
        assert envelope.sha256 == sha256(raw).hexdigest()
        assert session.get(WorkIntent, result.work_id) is not None
        assert session.execute(select(TenantState.decision_revision)).scalar_one() == 1
        assert session.execute(
            select(AuditEvent).where(AuditEvent.event_type == "RECEIVED")
        ).scalar_one().subject_id == str(result.envelope_id)


def test_replay_is_same_identity_but_different_bytes_conflict(
    runtime_factory, alpha, settings
) -> None:
    first = _accept(runtime_factory, alpha, settings, b"same", "same-key")
    replay = _accept(runtime_factory, alpha, settings, b"same", "same-key")
    assert replay.replayed is True
    assert replay.envelope_id == first.envelope_id
    with pytest.raises(IdempotencyConflict):
        _accept(runtime_factory, alpha, settings, b"different", "same-key")


def test_transaction_rollback_leaves_no_half_accepted_state(
    runtime_factory, alpha, settings
) -> None:
    with pytest.raises(RuntimeError):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, alpha.org_id)
            accept_input(
                session,
                alpha,
                b"will rollback",
                "application/octet-stream",
                "x",
                "rollback-key",
                settings,
            )
            raise RuntimeError("simulated crash before commit")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert session.execute(select(RawEnvelope)).scalars().all() == []
        assert session.execute(select(WorkIntent)).scalars().all() == []


def test_ordinary_mixed_tenant_input_is_preserved_but_quarantined(
    runtime_factory, alpha, settings
) -> None:
    raw = b"org_id,value\norg_demo_alpha,1\norg_demo_bravo,2\n"
    result = _accept(runtime_factory, alpha, settings, raw, "mixed")
    assert result.validation_status == "QUARANTINED"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        envelope = session.get(RawEnvelope, result.envelope_id)
        assert envelope is not None
        assert envelope.raw_bytes == raw
        assert envelope.declared_source_orgs == ["org_demo_alpha", "org_demo_bravo"]
        assert envelope.org_id == "org_demo_alpha"
