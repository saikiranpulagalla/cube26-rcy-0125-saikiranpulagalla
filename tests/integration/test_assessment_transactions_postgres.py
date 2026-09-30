from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import sleep
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_assessment_gate_postgres import (
    _add_active_pursuit,
    _add_settlement,
    _assess_synthetic_candidate,
)

from recovery_manager.assessment import (
    assess_synthetic,
    current_assessment,
    reserve_synthetic_packet,
)
from recovery_manager.db import set_local_tenant
from recovery_manager.ledger import LedgerContributorProof
from recovery_manager.models import (
    AmountDerivation,
    ClaimPursuit,
    CurrentRecoveryRecommendation,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceLifecycleEvent,
    PolicySourceVersion,
    PursuitAllocation,
    RecoveryAssessment,
    SettlementAllocation,
    SettlementReversal,
    SyntheticPacketReservation,
    TenantState,
)


def _ready_assessment(
    runtime_factory,
    worker_factory,
    settings,
    org_id: str,
    key: str,
    *,
    policy_effective_to: datetime | None = None,
) -> tuple[UUID, UUID]:
    """Construct and publish through the real worker-only guarded path."""
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session, settings, org_id=org_id, policy_effective_to=policy_effective_to
        )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(
            session,
            org_id,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
        assert assessment.recoverable_minor == 200
        return assessment.id, obligation_id


def test_export_rejects_policy_expired_after_assessment_without_revision_change(
    runtime_factory, worker_factory, settings
) -> None:
    """Time alone must invalidate a current synthetic assessment for export."""
    org_id = "org_synthetic_export_policy_expiry"
    assessment_id, obligation_id = _ready_assessment(
        runtime_factory,
        worker_factory,
        settings,
        org_id,
        "expiry",
        policy_effective_to=datetime.now(UTC) + timedelta(seconds=1),
    )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "CURRENT"  # type: ignore[union-attr]

    sleep(1.1)

    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, "expiry")


def test_assessment_snapshot_pins_exact_decision_dependencies(
    runtime_factory, worker_factory, settings
) -> None:
    assessment_id, _ = _ready_assessment(
        runtime_factory, worker_factory, settings, "org_synthetic_snapshot", "snapshot"
    )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_synthetic_snapshot")
        assessment = session.execute(
            select(RecoveryAssessment.dependency_snapshot).where(RecoveryAssessment.id == assessment_id)
        ).scalar_one()
    assert assessment["assessment_as_of"]
    assert assessment["obligation"]["id"]
    assert assessment["financial_event"]["source_record_version_id"]
    assert assessment["amount_derivation"]["id"]
    assert assessment["trusted_fixture_profile"]["fixture_sha256"]
    assert assessment["policy_source_version_id"] == assessment["policy"]["id"]
    assert assessment["evidence"][0]["source_record_version_id"]
    assert {"SETTLEMENT", "PURSUIT"} <= set(assessment["reconciliation"])
    assert assessment["ledger"]["remaining_minor"] == 200
    assert assessment["ledger_proof"]["consistent"] is True
    assert assessment["ledger_proof"]["settlement"]["contributors"] == []
    assert assessment["ledger_proof"]["pursuit"]["contributors"] == []


def test_assessment_snapshot_pins_decisive_ledger_contributors_and_historical_state(
    runtime_factory, worker_factory, settings
) -> None:
    """Historical ledger proof is self-contained and never follows later mutable rows."""
    org_id = "org_synthetic_ledger_contributor_snapshot"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state="RECONCILED_COMPLETE",
            pursuit_reconciliation_state="RECONCILED_COMPLETE",
        )
        _add_settlement(session, org_id, obligation_id, 50)
        _add_settlement(session, org_id, obligation_id, 25)
        _add_active_pursuit(session, org_id, obligation_id, 10)
        _add_active_pursuit(session, org_id, obligation_id, 15)
        pursuit_ids = session.execute(
            select(ClaimPursuit.id).where(ClaimPursuit.org_id == org_id)
        ).scalars().all()
        session.execute(
            text("UPDATE claim_pursuit SET status = 'EXPORTED' WHERE id = ANY(:ids)"),
            {"ids": pursuit_ids},
        )
        session.execute(
            text("UPDATE claim_pursuit SET status = 'PENDING' WHERE id = ANY(:ids)"),
            {"ids": pursuit_ids},
        )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(
            session,
            org_id,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
        assert assessment.recoverable_minor == 100
        snapshot = assessment.dependency_snapshot

    proof = snapshot["ledger_proof"]
    settlements = proof["settlement"]["contributors"]
    pursuits = proof["pursuit"]["contributors"]
    assert sorted(item["net_minor"] for item in settlements) == [25, 50]
    assert sum(item["net_minor"] for item in settlements) == proof["settlement"]["net_minor"] == 75
    assert {item["obligation_id"] for item in settlements} == {str(obligation_id)}
    assert all(item["credit_event_id"] and item["credit_source_record_version_id"] for item in settlements)
    assert sorted(item["allocated_minor"] for item in pursuits) == [10, 15]
    assert sum(item["allocated_minor"] for item in pursuits) == proof["pursuit"]["active_minor"] == 25
    assert {item["pursuit_state"] for item in pursuits} == {"PENDING"}
    assert all(item["pursuit_id"] and item["allocation_id"] for item in pursuits)
    assert proof["settlement"]["reconciliation"]["state"] == "RECONCILED_COMPLETE"
    assert proof["pursuit"]["reconciliation"]["state"] == "RECONCILED_COMPLETE"

    pursuit_id = UUID(pursuits[0]["pursuit_id"])
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.execute(
            text("UPDATE claim_pursuit SET status = 'RESOLVED' WHERE id = :id"), {"id": pursuit_id}
        )
        _add_settlement(session, org_id, obligation_id, 10)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        persisted_snapshot = session.execute(
            select(RecoveryAssessment.dependency_snapshot).where(RecoveryAssessment.id == assessment.id)
        ).scalar_one()
        assert persisted_snapshot == snapshot
        assert persisted_snapshot["ledger_proof"]["pursuit"]["contributors"][0]["pursuit_state"] == "PENDING"
        assert session.execute(
            select(ClaimPursuit.status).where(ClaimPursuit.id == pursuit_id)
        ).scalar_one() == "RESOLVED"
        assert session.execute(
            select(func.count())
            .select_from(SettlementAllocation)
            .where(SettlementAllocation.obligation_id == obligation_id)
        ).scalar_one() == 3


def test_ledger_snapshot_excludes_contributors_for_a_different_obligation(
    runtime_factory, worker_factory, settings
) -> None:
    """A same-tenant allocation is still irrelevant unless it names this obligation."""
    org_id = "org_synthetic_snapshot_wrong_obligation"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state="RECONCILED_COMPLETE",
            pursuit_reconciliation_state="RECONCILED_COMPLETE",
        )
        financial_event_id = session.execute(
            select(EconomicObligation.financial_event_id).where(EconomicObligation.id == obligation_id)
        ).scalar_one()
        other = EconomicObligation(
            org_id=org_id,
            economic_key="SYN-FEE-001-other-obligation",
            financial_event_id=financial_event_id,
            recovery_basis="OTHER_SUPPORTED",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(other)
        session.flush()
        session.add(
            AmountDerivation(
                org_id=org_id,
                obligation_id=other.id,
                derivation_version=1,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={},
            )
        )
        session.flush()
        _add_settlement(session, org_id, other.id, 50)
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(
            session,
            org_id,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
        assert assessment.recoverable_minor == 200
        assert assessment.dependency_snapshot["ledger_proof"]["settlement"]["contributors"] == []


def test_assessment_snapshot_pins_settlement_reversal_provenance(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_snapshot_settlement_reversal"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state="RECONCILED_COMPLETE",
            pursuit_reconciliation_state="RECONCILED_COMPLETE",
        )
        _add_settlement(session, org_id, obligation_id, 100)
        session.flush()
        allocation = session.execute(
            select(SettlementAllocation).where(SettlementAllocation.obligation_id == obligation_id)
        ).scalar_one()
        reversal = SettlementReversal(
            org_id=org_id,
            allocation_id=allocation.id,
            reversed_minor=25,
            reason="historical correction",
        )
        session.add(reversal)
        session.flush()
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(
            session,
            org_id,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        contributor = assessment.dependency_snapshot["ledger_proof"]["settlement"]["contributors"][0]
        assert assessment.recoverable_minor == 125
        assert contributor["allocated_minor"] == 100
        assert contributor["reversed_minor"] == 25
        assert contributor["net_minor"] == 75
        assert contributor["reversals"] == [
            {"id": str(reversal.id), "reversed_minor": 25, "reason": "historical correction"}
        ]


def test_cross_tenant_settlement_contributor_is_rejected(runtime_factory, settings) -> None:
    """Composite tenant foreign keys prevent cross-tenant ledger provenance."""
    org_a = "org_synthetic_snapshot_cross_tenant_a"
    org_b = "org_synthetic_snapshot_cross_tenant_b"
    with runtime_factory() as session, session.begin():
        obligation_a = _assess_synthetic_candidate(session, settings, org_id=org_a)
        obligation_b = _assess_synthetic_candidate(session, settings, org_id=org_b)
        credit_event_b = session.execute(
            select(EconomicObligation.financial_event_id).where(EconomicObligation.id == obligation_b)
        ).scalar_one()
        set_local_tenant(session, org_a)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(
                    SettlementAllocation(
                        org_id=org_a,
                        credit_event_id=credit_event_b,
                        obligation_id=obligation_a,
                        allocated_minor=1,
                        rationale="cross-tenant contributor must fail",
                    )
                )
                session.flush()


def test_inconsistent_ledger_contributor_proof_cannot_be_claim_ready(
    runtime_factory, worker_factory, settings, monkeypatch
) -> None:
    """A snapshot integrity disagreement is an execution-time REVIEW, never normalization."""
    org_id = "org_synthetic_snapshot_inconsistent_proof"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    monkeypatch.setattr(
        "recovery_manager.assessment.ledger_contributor_proof",
        lambda *args, **kwargs: LedgerContributorProof(
            settlement={"net_minor": 75, "contributors": []},
            pursuit={"active_minor": 0, "contributors": []},
            consistent=False,
        ),
    )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(
            session,
            org_id,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
    assert assessment.conclusion == "REVIEW"
    assert assessment.recoverable_minor is None


def test_export_rejects_pinned_policy_revoked_after_publication(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_export_policy_revoked"
    assessment_id, obligation_id = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "policy-revoked"
    )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        original_snapshot = session.execute(
            select(RecoveryAssessment.dependency_snapshot).where(RecoveryAssessment.id == assessment_id)
        ).scalar_one()
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import sessionmaker

    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.execute(
            update(PolicySourceVersion)
            .where(PolicySourceVersion.org_id == org_id)
            .values(lifecycle_state="REVOKED")
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(
            select(RecoveryAssessment.dependency_snapshot).where(RecoveryAssessment.id == assessment_id)
        ).scalar_one() == original_snapshot
        assert original_snapshot["policy"]["lifecycle_state"] == "ACTIVE"
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, "policy-revoked")


def test_export_rejects_decisive_evidence_revoked_after_publication(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_export_evidence_revoked"
    assessment_id, obligation_id = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "evidence-revoked"
    )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assertion_id = session.execute(select(EvidenceAssertion.id)).scalar_one()
        session.add(
            EvidenceLifecycleEvent(
                org_id=org_id,
                assertion_id=assertion_id,
                state="REVOKED",
                reason="repair-08 decisive dependency changed",
            )
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, "evidence-revoked")


def test_simultaneous_valid_publications_leave_one_coherent_current_pointer(
    runtime_factory, worker_factory, settings
) -> None:
    """The existing tenant lock serializes concurrent worker publication attempts."""
    org_id = "org_synthetic_simultaneous_publication"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    barrier = Barrier(2)

    def publish() -> UUID:
        with worker_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            barrier.wait(timeout=5)
            assessment = assess_synthetic(
                session,
                org_id,
                obligation_id,
                "SYN-FEE-001",
                "synthetic-invalid-fee",
                synthetic_capability_enabled=True,
            )
            assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
            return assessment.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assessment_ids = list(executor.map(lambda _: publish(), range(2)))
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        current_id = session.execute(
            select(CurrentRecoveryRecommendation.assessment_id).where(
                CurrentRecoveryRecommendation.org_id == org_id,
                CurrentRecoveryRecommendation.obligation_id == obligation_id,
            )
        ).scalar_one()
        assert current_id in assessment_ids
        assert session.execute(
            select(func.count())
            .select_from(CurrentRecoveryRecommendation)
            .where(CurrentRecoveryRecommendation.obligation_id == obligation_id)
        ).scalar_one() == 1


def test_policy_revocation_racing_publication_leaves_old_result_stale(
    runtime_factory, worker_factory, settings
) -> None:
    """A decisive mutation waits behind publication, then invalidates its revision."""
    org_id = "org_synthetic_publication_policy_race"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    barrier = Barrier(2)

    def publish() -> UUID:
        with worker_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SELECT public.lock_current_tenant_revision()"))
            barrier.wait(timeout=5)
            sleep(0.25)
            return assess_synthetic(
                session,
                org_id,
                obligation_id,
                "SYN-FEE-001",
                "synthetic-invalid-fee",
                synthetic_capability_enabled=True,
            ).id

    def revoke() -> None:
        from sqlalchemy import create_engine, update
        from sqlalchemy.orm import sessionmaker

        owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
        with owner_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            barrier.wait(timeout=5)
            session.execute(
                update(PolicySourceVersion)
                .where(PolicySourceVersion.org_id == org_id)
                .values(lifecycle_state="REVOKED")
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        publication = executor.submit(publish)
        revocation = executor.submit(revoke)
        assessment_id = publication.result(timeout=10)
        revocation.result(timeout=10)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, "publication-policy-race")


@pytest.mark.parametrize("change", ("settlement", "pursuit"))
def test_ledger_change_racing_publication_leaves_old_result_stale(
    runtime_factory, worker_factory, settings, change: str
) -> None:
    """Tenant-first locking prevents a pre-ledger residual from remaining actionable."""
    org_id = f"org_synthetic_publication_{change}_race"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state="RECONCILED_COMPLETE",
            pursuit_reconciliation_state="RECONCILED_COMPLETE",
        )
    barrier = Barrier(2)

    def publish() -> UUID:
        with worker_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SELECT public.lock_current_tenant_revision()"))
            barrier.wait(timeout=5)
            sleep(0.25)
            return assess_synthetic(
                session,
                org_id,
                obligation_id,
                "SYN-FEE-001",
                "synthetic-invalid-fee",
                synthetic_capability_enabled=True,
            ).id

    def mutate_ledger() -> None:
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            barrier.wait(timeout=5)
            if change == "settlement":
                _add_settlement(session, org_id, obligation_id, 100)
            else:
                _add_active_pursuit(session, org_id, obligation_id, 100)

    with ThreadPoolExecutor(max_workers=2) as executor:
        publication = executor.submit(publish)
        mutation = executor.submit(mutate_ledger)
        assessment_id = publication.result(timeout=10)
        mutation.result(timeout=10)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, f"publication-{change}-race")


@pytest.mark.parametrize("change", ("settlement", "pursuit"))
def test_ledger_change_first_blocks_publication_until_new_residual_is_assessed(
    runtime_factory, worker_factory, settings, change: str
) -> None:
    """The complementary schedule cannot publish the pre-mutation USD 2.00 residual."""
    org_id = f"org_synthetic_{change}_first_publication_race"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state="RECONCILED_COMPLETE",
            pursuit_reconciliation_state="RECONCILED_COMPLETE",
        )
    barrier = Barrier(2)

    def mutate_first() -> None:
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            session.execute(text("SELECT public.lock_current_tenant_revision()"))
            barrier.wait(timeout=5)
            if change == "settlement":
                _add_settlement(session, org_id, obligation_id, 100)
            else:
                _add_active_pursuit(session, org_id, obligation_id, 100)

    def publish_second() -> UUID:
        with worker_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            barrier.wait(timeout=5)
            assessment = assess_synthetic(
                session,
                org_id,
                obligation_id,
                "SYN-FEE-001",
                "synthetic-invalid-fee",
                synthetic_capability_enabled=True,
            )
            assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
            assert assessment.recoverable_minor == 100
            return assessment.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        mutation = executor.submit(mutate_first)
        publication = executor.submit(publish_second)
        mutation.result(timeout=10)
        assessment_id = publication.result(timeout=10)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        current = current_assessment(session, org_id, obligation_id)
        assert current is not None and current.assessment.id == assessment_id
        assert current.assessment.recoverable_minor == 100


def test_policy_revocation_first_blocks_publication_of_claim_ready_assessment(
    runtime_factory, worker_factory, settings
) -> None:
    """A policy mutation holding the decisive lock forces the later publisher to REVIEW."""
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import sessionmaker

    org_id = "org_synthetic_policy_first_publication_race"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    barrier = Barrier(2)

    def revoke_first() -> None:
        owner_factory = sessionmaker(
            bind=create_engine(settings.migration_database_url, future=True), future=True
        )
        with owner_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            session.execute(text("SELECT public.lock_current_tenant_revision()"))
            barrier.wait(timeout=5)
            session.execute(
                update(PolicySourceVersion)
                .where(PolicySourceVersion.org_id == org_id)
                .values(lifecycle_state="REVOKED")
            )

    def publish_second() -> str:
        with worker_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            barrier.wait(timeout=5)
            return assess_synthetic(
                session,
                org_id,
                obligation_id,
                "SYN-FEE-001",
                "synthetic-invalid-fee",
                synthetic_capability_enabled=True,
            ).conclusion

    with ThreadPoolExecutor(max_workers=2) as executor:
        revocation = executor.submit(revoke_first)
        publication = executor.submit(publish_second)
        revocation.result(timeout=10)
        assert publication.result(timeout=10) == "REVIEW"


def test_policy_revocation_first_rejects_later_export(runtime_factory, worker_factory, settings) -> None:
    """The complementary invalidation-first schedule cannot reserve a stale packet."""
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import sessionmaker

    org_id = "org_synthetic_policy_first_export_race"
    assessment_id, _ = _ready_assessment(runtime_factory, worker_factory, settings, org_id, "policy-first")
    barrier = Barrier(2)

    def revoke_first() -> None:
        owner_factory = sessionmaker(
            bind=create_engine(settings.migration_database_url, future=True), future=True
        )
        with owner_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            session.execute(text("SELECT public.lock_current_tenant_revision()"))
            barrier.wait(timeout=5)
            session.execute(
                update(PolicySourceVersion)
                .where(PolicySourceVersion.org_id == org_id)
                .values(lifecycle_state="REVOKED")
            )

    def export_second() -> str:
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            barrier.wait(timeout=5)
            with pytest.raises(ValueError, match="not current synthetic claim-ready"):
                reserve_synthetic_packet(session, org_id, assessment_id, "policy-first-export")
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        revocation = executor.submit(revoke_first)
        export = executor.submit(export_second)
        revocation.result(timeout=10)
        assert export.result(timeout=10) == "rejected"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(select(func.count()).select_from(SyntheticPacketReservation)).scalar_one() == 0


def test_policy_revocation_racing_export_commits_only_historical_valid_packet(
    runtime_factory, worker_factory, settings
) -> None:
    """Export holds the tenant lock through packet persistence; later revocation stales future action."""
    org_id = "org_synthetic_export_policy_race"
    assessment_id, obligation_id = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "export-policy-race"
    )
    barrier = Barrier(2)

    def export() -> UUID:
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SELECT public.lock_current_tenant_revision()"))
            barrier.wait(timeout=5)
            sleep(0.25)
            return reserve_synthetic_packet(session, org_id, assessment_id, "export-policy-race").id

    def revoke() -> None:
        from sqlalchemy import create_engine, update
        from sqlalchemy.orm import sessionmaker

        owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
        with owner_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            barrier.wait(timeout=5)
            session.execute(
                update(PolicySourceVersion)
                .where(PolicySourceVersion.org_id == org_id)
                .values(lifecycle_state="REVOKED")
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        exported = executor.submit(export)
        revocation = executor.submit(revoke)
        packet_id = exported.result(timeout=10)
        revocation.result(timeout=10)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(
            select(SyntheticPacketReservation.id).where(SyntheticPacketReservation.id == packet_id)
        ).scalar_one() == packet_id
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, assessment_id, "export-policy-race-after-revocation")


def test_concurrent_exports_reserve_one_economic_value(runtime_factory, worker_factory, settings) -> None:
    org_id = "org_synthetic_export_race"
    assessment_id, obligation_id = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "export-race"
    )
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


def test_same_key_concurrent_export_is_one_packet(runtime_factory, worker_factory, settings) -> None:
    org_id = "org_synthetic_export_idempotency"
    assessment_id, _ = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "export-idempotency"
    )
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


def test_export_rejects_stale_assessment_after_decisive_revision(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_export_stale"
    assessment_id, obligation_id = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "export-stale"
    )
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


def test_restricted_runtime_cannot_mutate_immutable_assessments_or_packets(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_immutable"
    assessment_id, _ = _ready_assessment(
        runtime_factory, worker_factory, settings, org_id, "immutable"
    )
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


def test_export_transaction_rolls_back_packet_pursuit_and_allocation(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_export_rollback"
    assessment_id, _ = _ready_assessment(runtime_factory, worker_factory, settings, org_id, "rollback")
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


def test_decision_revision_advances_on_commit_but_not_rollback(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_synthetic_revision"
    _ready_assessment(runtime_factory, worker_factory, settings, org_id, "revision")
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
