from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from recovery_manager.assessment import (
    assess_synthetic,
    current_assessment,
    reserve_synthetic_packet,
)
from recovery_manager.config import Principal
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    AmountDerivation,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceRecord,
    FinancialEvent,
    PolicySourceVersion,
    ReconciliationState,
    SourceRecordVersion,
    SyntheticFixtureProfile,
)


def _register_synthetic_profile(settings, org_id: str) -> None:
    owner_factory = sessionmaker(
        bind=create_engine(settings.migration_database_url, future=True), future=True
    )
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": "a" * 64}
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        policy = PolicySourceVersion(
            org_id=org_id,
            policy_key="SYN-VALID-FEE-8",
            authority_class="SYNTHETIC",
            content_sha256="b" * 64,
            effective_from=None,
            effective_to=None,
            applicability={
                **provenance,
                "proposition_key": "synthetic-invalid-fee",
                "permitted_amount_minor": 800,
            },
            raw_text="SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY",
            lifecycle_state="ACTIVE",
        )
        session.add(policy)
        session.flush()
        session.add(
            SyntheticFixtureProfile(
                org_id=org_id,
                fixture_profile=provenance["fixture_profile"],
                fixture_sha256=provenance["fixture_sha256"],
                policy_source_version_id=policy.id,
            )
        )


def _assess_synthetic_candidate(
    session,
    settings,
    *,
    org_id: str,
    premise_key: str = "SYNTHETIC_VALID_FEE",
    evidence_quantity: int = 1,
    source_value: bool = True,
    asserted_value: bool = True,
    subject_key: str = "SYN-FEE-001",
    mismatched_source_version: bool = False,
    polarity: str = "SUPPORTS",
    reconciliation_state: str | None = "RECONCILED_NONE",
):
    """Build a complete mechanics candidate, varying one proof premise at a time."""
    _register_synthetic_profile(settings, org_id)
    set_local_tenant(session, org_id)
    accept_input(
        session,
        Principal(org_id=org_id, actor_id="synthetic_fixture", role="fixture_admin"),
        b"synthetic fixture",
        "application/octet-stream",
        "synthetic",
        f"candidate-{org_id}",
        settings,
    )
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": "a" * 64}
    source = SourceRecordVersion(
        org_id=org_id,
        source_kind="synthetic",
        source_record_id="SYN-FEE-001",
        content_sha256="c" * 64,
        declared_org_id=org_id,
        payload={"fee": {"valid": source_value}},
    )
    session.add(source)
    session.flush()
    event = FinancialEvent(
        org_id=org_id,
        source_record_version_id=source.id,
        event_type="SYNTHETIC_FEE",
        direction="DEBIT",
        amount_minor=1000,
        currency="USD",
        quantity=1,
        posting_time=None,
        posting_time_precision=None,
        incident_time=None,
        incident_time_precision=None,
        business_references={"synthetic_fixture": "SYN-FEE-001"},
        normalized_fields={"synthetic": True},
    )
    evidence = EvidenceRecord(
        org_id=org_id,
        source_record_version_id=source.id,
        evidence_kind="SYNTHETIC",
        observed_time=None,
        observed_time_precision=None,
        coverage_quantity=evidence_quantity,
        coverage_scope={"coverage": "KNOWN"},
        normalized_fields={},
    )
    session.add(event)
    session.flush()
    obligation = EconomicObligation(
        org_id=org_id,
        economic_key="SYN-FEE-001",
        financial_event_id=event.id,
        recovery_basis="INVALID_FEE",
        currency="USD",
        business_instance=provenance,
        quantity_scope={"coverage": "KNOWN", "quantity": "1"},
    )
    session.add_all((evidence, obligation))
    session.flush()
    if reconciliation_state is not None:
        session.add_all(
            (
                ReconciliationState(
                    org_id=org_id,
                    obligation_id=obligation.id,
                    domain="SETTLEMENT",
                    state=reconciliation_state,
                    cutoff=None,
                    source_set_sha256="e" * 64,
                ),
                ReconciliationState(
                    org_id=org_id,
                    obligation_id=obligation.id,
                    domain="PURSUIT",
                    state=reconciliation_state,
                    cutoff=None,
                    source_set_sha256="f" * 64,
                ),
            )
        )
        session.flush()
    assertion_source_id = source.id
    if mismatched_source_version:
        wrong_source = SourceRecordVersion(
            org_id=org_id,
            source_kind="synthetic-proof",
            source_record_id="SYN-FEE-001-proof",
            content_sha256="d" * 64,
            declared_org_id=org_id,
            payload={"fee": {"valid": asserted_value}},
        )
        session.add(wrong_source)
        session.flush()
        assertion_source_id = wrong_source.id
    session.add_all(
        (
            EvidenceAssertion(
                org_id=org_id,
                evidence_record_id=evidence.id,
                source_record_version_id=assertion_source_id,
                proposition_key="synthetic-invalid-fee",
                subject_key=subject_key,
                polarity=polarity,
                fact_path="fee.valid",
                asserted_value=asserted_value,
                scope={"coverage": "KNOWN", "premise_key": premise_key},
                decisive=True,
            ),
            AmountDerivation(
                org_id=org_id,
                obligation_id=obligation.id,
                derivation_version=1,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis=provenance,
            ),
        )
    )
    session.flush()
    return obligation.id


@pytest.mark.parametrize(
    ("kwargs", "org_id"),
    (
        ({"premise_key": "IRRELEVANT"}, "org_proof_irrelevant"),
        ({"evidence_quantity": 0}, "org_proof_insufficient"),
        ({"source_value": False, "asserted_value": True}, "org_proof_false_value"),
        ({"mismatched_source_version": True}, "org_proof_wrong_source"),
        ({"subject_key": "other-business-instance"}, "org_proof_wrong_subject"),
        ({"polarity": "CONTRADICTS"}, "org_proof_contradictory"),
        ({"reconciliation_state": None}, "org_reconciliation_absent"),
        ({"reconciliation_state": "UNKNOWN"}, "org_reconciliation_unknown"),
    ),
)
def test_synthetic_readiness_requires_exact_required_evidence_premise(
    runtime_factory, worker_factory, settings, kwargs, org_id
) -> None:
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id, **kwargs)
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


def test_worker_constructor_publishes_trusted_synthetic_two_dollar_control(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_worker_synthetic_control"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
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


def test_export_consumes_real_worker_published_synthetic_assessment(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_worker_synthetic_export"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
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
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, org_id, uuid4(), "fabricated-export")
        packet = reserve_synthetic_packet(session, org_id, assessment.id, "worker-path-export")
        assert packet.packet["synthetic_only"] is True
        assert packet.packet["amount_minor"] == 200


def test_empty_decisive_evidence_cannot_produce_synthetic_ready(
    runtime_factory, worker_factory, alpha, settings
) -> None:
    """Regression: a residual plus a synthetic-looking derivation is not proof."""
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        accept_input(
            session,
            alpha,
            b"synthetic assessment fixture",
            "application/octet-stream",
            "synthetic-fixture",
            "synthetic-assessment-fixture",
            settings,
        )
        obligation = EconomicObligation(
            org_id="org_demo_alpha",
            economic_key="SYN-FEE-001",
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={
                "fixture_label": "SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY"
            },
            quantity_scope={"coverage": "KNOWN", "quantity": "1"},
        )
        session.add(obligation)
        session.flush()
        session.add(
            AmountDerivation(
                org_id="org_demo_alpha",
                obligation_id=obligation.id,
                derivation_version=1,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={"synthetic_policy_id": "SYN-VALID-FEE-8"},
            )
        )
        session.flush()
        obligation_id = obligation.id
    with worker_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        assessment = assess_synthetic(
            session,
            "org_demo_alpha",
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
        )
        assert assessment.conclusion != "SYNTHETIC_CLAIM_READY"
        assert assessment.conclusion == "REVIEW"


def test_trusted_synthetic_control_is_ready_for_two_dollars(
    runtime_factory, worker_factory, alpha, settings
) -> None:
    """SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY."""
    synthetic_org = "org_synthetic_mechanics_converted"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=synthetic_org)
    with worker_factory() as session, session.begin():
        set_local_tenant(session, synthetic_org)
        disabled = assess_synthetic(
            session,
            synthetic_org,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
        )
        assert disabled.conclusion == "REVIEW"
        wrong_subject = assess_synthetic(
            session,
            synthetic_org,
            obligation_id,
            "other-business-instance",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert wrong_subject.conclusion == "REVIEW"
        assessment = assess_synthetic(
            session,
            synthetic_org,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
        assert assessment.recoverable_minor == 200
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, synthetic_org)
        packet = reserve_synthetic_packet(session, synthetic_org, assessment.id, "synthetic-export")
        assert packet.packet["synthetic_only"] is True
        assert packet.packet["amount_minor"] == 200
        assert (
            reserve_synthetic_packet(session, synthetic_org, assessment.id, "synthetic-export").id
            == packet.id
        )
        assert current_assessment(session, synthetic_org, obligation_id).state == "STALE"  # type: ignore[union-attr]
        session.add(
            AmountDerivation(
                org_id=synthetic_org,
                obligation_id=obligation_id,
                derivation_version=2,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=300,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": "a" * 64},
            )
        )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, synthetic_org)
        forged_entitlement = assess_synthetic(
            session,
            synthetic_org,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert forged_entitlement.conclusion == "REVIEW"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, synthetic_org)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    update(PolicySourceVersion)
                    .where(
                        PolicySourceVersion.org_id == synthetic_org,
                        PolicySourceVersion.policy_key == "SYN-VALID-FEE-8",
                    )
                    .values(lifecycle_state="REVOKED")
                )
        session.add(
            AmountDerivation(
                org_id=synthetic_org,
                obligation_id=obligation_id,
                derivation_version=3,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": "e" * 64},
            )
        )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, synthetic_org)
        unregistered_fixture = assess_synthetic(
            session,
            synthetic_org,
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert unregistered_fixture.conclusion == "REVIEW"


def test_runtime_cannot_register_synthetic_policy_authority(runtime_factory, settings) -> None:
    org_id = "org_untrusted_synthetic_policy"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        accept_input(
            session,
            Principal(org_id=org_id, actor_id="runtime", role="operator"),
            b"ordinary input",
            "application/octet-stream",
            "ordinary",
            "untrusted-policy",
            settings,
        )
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(
                    PolicySourceVersion(
                        org_id=org_id,
                        policy_key="SYN-VALID-FEE-8",
                        authority_class="SYNTHETIC",
                        content_sha256="d" * 64,
                        effective_from=None,
                        effective_to=None,
                        applicability={"fixture_profile": "synthetic-mechanics-v1"},
                        raw_text="forged synthetic authority",
                        lifecycle_state="ACTIVE",
                    )
                )
                session.flush()
