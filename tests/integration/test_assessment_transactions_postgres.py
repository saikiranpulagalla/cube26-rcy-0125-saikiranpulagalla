from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from recovery_manager.assessment import current_assessment, reserve_synthetic_packet
from recovery_manager.config import Principal
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    ClaimPursuit,
    CurrentRecoveryRecommendation,
    EconomicObligation,
    PursuitAllocation,
    RecoveryAssessment,
    SyntheticPacketReservation,
    TenantState,
)


def _ready_assessment(runtime_factory, settings, org_id: str, key: str) -> tuple[UUID, UUID]:
    principal = Principal(org_id=org_id, actor_id="synthetic_fixture", role="fixture_admin")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        accept_input(
            session, principal, key.encode(), "application/octet-stream", "synthetic", key, settings
        )
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key=key,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={"fixture": "SYNTHETIC MECHANICS"},
            quantity_scope={"coverage": "KNOWN", "quantity": "1"},
        )
        session.add(obligation)
        session.flush()
        revision = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one()
        assessment = RecoveryAssessment(
            org_id=org_id,
            obligation_id=obligation.id,
            tenant_revision=revision,
            conclusion="SYNTHETIC_CLAIM_READY",
            recoverable_minor=200,
            currency="USD",
            dependency_snapshot={"tenant_revision": revision},
        )
        session.add(assessment)
        session.flush()
        session.add(
            CurrentRecoveryRecommendation(
                org_id=org_id, obligation_id=obligation.id, assessment_id=assessment.id
            )
        )
        return assessment.id, obligation.id


def test_concurrent_exports_reserve_one_economic_value(runtime_factory, settings) -> None:
    org_id = "org_synthetic_export_race"
    assessment_id, obligation_id = _ready_assessment(runtime_factory, settings, org_id, "export-race")
    barrier = Barrier(2)

    def export(key: str) -> str:
        try:
            with runtime_factory() as session, session.begin():
                set_local_tenant(session, org_id)
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                barrier.wait(timeout=5)
                return str(reserve_synthetic_packet(session, org_id, assessment_id, key).id)
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(export, ("different-a", "different-b")))
    assert results.count("rejected") == 1
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(select(func.count()).select_from(SyntheticPacketReservation)).scalar_one() == 1
        assert session.execute(select(func.sum(PursuitAllocation.allocated_minor))).scalar_one() == 200
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]


def test_same_key_concurrent_export_is_one_packet(runtime_factory, settings) -> None:
    org_id = "org_synthetic_export_idempotency"
    assessment_id, _ = _ready_assessment(runtime_factory, settings, org_id, "export-idempotency")
    barrier = Barrier(2)

    def export() -> str:
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            barrier.wait(timeout=5)
            return str(reserve_synthetic_packet(session, org_id, assessment_id, "same-key").id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        packet_ids = list(executor.map(lambda _: export(), range(2)))
    assert packet_ids[0] == packet_ids[1]
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(select(func.count()).select_from(SyntheticPacketReservation)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(ClaimPursuit)).scalar_one() == 1


def test_export_rejects_stale_assessment_after_decisive_revision(runtime_factory, settings) -> None:
    org_id = "org_synthetic_export_stale"
    assessment_id, obligation_id = _ready_assessment(runtime_factory, settings, org_id, "export-stale")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "CURRENT"  # type: ignore[union-attr]
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.add(
            ClaimPursuit(
                org_id=org_id,
                external_reference=None,
                status="RECOMMENDED",
                currency="USD",
                declared_minor=1,
            )
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, "stale")


def test_restricted_runtime_cannot_mutate_immutable_assessments_or_packets(runtime_factory, settings) -> None:
    org_id = "org_synthetic_immutable"
    assessment_id, _ = _ready_assessment(runtime_factory, settings, org_id, "immutable")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        packet = reserve_synthetic_packet(session, org_id, assessment_id, "immutable-export")
        packet_id = packet.id
    for statement in (
        text("UPDATE recovery_assessment SET recoverable_minor = 1 WHERE id = :id"),
        text("DELETE FROM recovery_assessment WHERE id = :id"),
        text("UPDATE synthetic_packet_reservation SET packet = '{}'::jsonb WHERE id = :id"),
        text("DELETE FROM synthetic_packet_reservation WHERE id = :id"),
    ):
        with runtime_factory() as session, pytest.raises(DBAPIError), session.begin():
            set_local_tenant(session, org_id)
            session.execute(statement, {"id": packet_id if "packet" in str(statement) else assessment_id})


def test_export_transaction_rolls_back_packet_pursuit_and_allocation(runtime_factory, settings) -> None:
    org_id = "org_synthetic_export_rollback"
    assessment_id, _ = _ready_assessment(runtime_factory, settings, org_id, "rollback")
    with pytest.raises(RuntimeError, match="inject"):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            reserve_synthetic_packet(session, org_id, assessment_id, "rollback-export")
            raise RuntimeError("inject before commit")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(select(func.count()).select_from(SyntheticPacketReservation)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(ClaimPursuit)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(PursuitAllocation)).scalar_one() == 0


def test_decision_revision_advances_on_commit_but_not_rollback(runtime_factory, settings) -> None:
    org_id = "org_synthetic_revision"
    _, _ = _ready_assessment(runtime_factory, settings, org_id, "revision")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        before = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one()
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.add(
            ClaimPursuit(
                org_id=org_id,
                external_reference=None,
                status="RECOMMENDED",
                currency="USD",
                declared_minor=1,
            )
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        after_commit = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one()
        assert after_commit > before
    with pytest.raises(RuntimeError, match="rollback"):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.add(
                ClaimPursuit(
                    org_id=org_id,
                    external_reference=None,
                    status="PENDING",
                    currency="USD",
                    declared_minor=1,
                )
            )
            session.flush()
            raise RuntimeError("rollback")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one() == after_commit
