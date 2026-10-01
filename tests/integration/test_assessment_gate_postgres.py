from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
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
    ClaimPursuit,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceLifecycleEvent,
    EvidenceRecord,
    FinancialEvent,
    PolicySourceVersion,
    PursuitAllocation,
    ReconciliationState,
    SettlementAllocation,
    SourceRecordVersion,
    SyntheticFixtureProfile,
)

_RECONCILIATION_DEFAULT = object()


def _register_synthetic_profile(
    settings,
    org_id: str,
    fixture_sha256: str,
    permitted_minor: int = 800,
    *,
    effective_to: datetime | None = None,
) -> None:
    owner_factory = sessionmaker(
        bind=create_engine(settings.migration_database_url, future=True), future=True
    )
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": fixture_sha256}
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        policy = PolicySourceVersion(
            org_id=org_id,
            policy_key="SYN-VALID-FEE-8",
            authority_class="SYNTHETIC",
            content_sha256="b" * 64,
            effective_from=None,
            effective_to=effective_to,
            applicability={
                **provenance,
                "proposition_key": "synthetic-invalid-fee",
                "permitted_amount_minor": permitted_minor,
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
    required_quantity: str = "1",
    source_value: object = True,
    asserted_value: object = True,
    fact_path: str = "fee.valid",
    subject_key: str = "SYN-FEE-001",
    mismatched_source_version: bool = False,
    polarity: str = "SUPPORTS",
    reconciliation_state: str | None = "RECONCILED_NONE",
    settlement_reconciliation_state: str | None | object = _RECONCILIATION_DEFAULT,
    pursuit_reconciliation_state: str | None | object = _RECONCILIATION_DEFAULT,
    fixture_payload: dict[str, object] | None = None,
    registered_fixture_payload: dict[str, object] | None = None,
    register_profile: bool = True,
    entitlement_minor: int = 200,
    event_amount_minor: int = 1000,
    event_direction: str = "DEBIT",
    permitted_minor: int = 800,
    derivation_currency: str = "USD",
    policy_effective_to: datetime | None = None,
):
    """Build a complete mechanics candidate, varying one proof premise at a time."""
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
    payload = fixture_payload if fixture_payload is not None else {"fee": {"valid": source_value}}
    actual_fixture_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    trusted_payload = registered_fixture_payload if registered_fixture_payload is not None else payload
    fixture_sha256 = hashlib.sha256(
        json.dumps(trusted_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": fixture_sha256}
    if register_profile:
        _register_synthetic_profile(
            settings,
            org_id,
            fixture_sha256,
            permitted_minor,
            effective_to=policy_effective_to,
        )
    source = SourceRecordVersion(
        org_id=org_id,
        source_kind="synthetic",
        source_record_id="SYN-FEE-001",
        content_sha256=actual_fixture_sha256,
        declared_org_id=org_id,
        payload=payload,
    )
    session.add(source)
    session.flush()
    event = FinancialEvent(
        org_id=org_id,
        source_record_version_id=source.id,
        event_type="SYNTHETIC_FEE",
        direction=event_direction,
        amount_minor=event_amount_minor,
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
        quantity_scope={"coverage": "KNOWN", "quantity": required_quantity},
    )
    session.add_all((evidence, obligation))
    session.flush()
    settlement_state = (
        reconciliation_state
        if settlement_reconciliation_state is _RECONCILIATION_DEFAULT
        else settlement_reconciliation_state
    )
    pursuit_state = (
        reconciliation_state
        if pursuit_reconciliation_state is _RECONCILIATION_DEFAULT
        else pursuit_reconciliation_state
    )
    states = (("SETTLEMENT", settlement_state, "e" * 64), ("PURSUIT", pursuit_state, "f" * 64))
    session.add_all(
        ReconciliationState(
            org_id=org_id,
            obligation_id=obligation.id,
            domain=domain,
            state=state,
            cutoff=datetime.now(UTC),
            source_set_sha256=source_set_sha256,
        )
        for domain, state, source_set_sha256 in states
        if state is not None
    )
    if settlement_state is not None or pursuit_state is not None:
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
                fact_path=fact_path,
                asserted_value=asserted_value,
                scope={"coverage": "KNOWN", "premise_key": premise_key},
                decisive=True,
            ),
            AmountDerivation(
                org_id=org_id,
                obligation_id=obligation.id,
                derivation_version=1,
                currency=derivation_currency,
                observed_amount_minor=event_amount_minor,
                expected_amount_minor=permitted_minor,
                justified_entitlement_minor=entitlement_minor,
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
        ({"source_value": False, "asserted_value": False}, "org_proof_false_support"),
        ({"source_value": 1, "asserted_value": True}, "org_proof_numeric_boolean_mismatch"),
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


@pytest.mark.parametrize(
    ("source_value", "asserted_value", "expected"),
    ((1, True, "REVIEW"), (True, True, "SYNTHETIC_CLAIM_READY")),
)
def test_live_readiness_requires_exact_json_evidence_value_type(
    runtime_factory, worker_factory, settings, source_value: object, asserted_value: object, expected: str
) -> None:
    org_id = f"org_json_evidence_type_{source_value!r}_{asserted_value!r}".replace(" ", "_")
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            source_value=source_value,
            asserted_value=asserted_value,
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
    assert assessment.conclusion == expected


@pytest.mark.parametrize(
    ("settlement_state", "pursuit_state"),
    (
        (None, "RECONCILED_NONE"),
        ("RECONCILED_NONE", None),
        ("UNKNOWN", "RECONCILED_NONE"),
        ("RECONCILED_NONE", "UNKNOWN"),
    ),
)
def test_unknown_reconciliation_in_either_operand_cannot_be_treated_as_zero(
    runtime_factory, worker_factory, settings, settlement_state: str | None, pursuit_state: str | None
) -> None:
    org_id = f"org_reconciliation_independent_{settlement_state}_{pursuit_state}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state=settlement_state,
            pursuit_reconciliation_state=pursuit_state,
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


def _add_settlement(session, org_id: str, obligation_id, amount_minor: int) -> None:
    fixture_key = f"settlement-{obligation_id}-{uuid4().hex}"
    source = SourceRecordVersion(
        org_id=org_id,
        source_kind="synthetic-settlement",
        source_record_id=fixture_key,
        content_sha256=hashlib.sha256(fixture_key.encode()).hexdigest(),
        declared_org_id=org_id,
        payload={"credit": {"amount_minor": amount_minor}},
    )
    session.add(source)
    session.flush()
    credit = FinancialEvent(
        org_id=org_id,
        source_record_version_id=source.id,
        event_type="SYNTHETIC_CREDIT",
        direction="CREDIT",
        amount_minor=amount_minor,
        currency="USD",
        quantity=None,
        posting_time=None,
        posting_time_precision=None,
        incident_time=None,
        incident_time_precision=None,
        business_references={},
        normalized_fields={"synthetic": True},
    )
    session.add(credit)
    session.flush()
    session.add(
        SettlementAllocation(
            org_id=org_id,
            credit_event_id=credit.id,
            obligation_id=obligation_id,
            allocated_minor=amount_minor,
            rationale="repair-07 known settlement",
        )
    )


def _add_active_pursuit(session, org_id: str, obligation_id, amount_minor: int) -> None:
    pursuit = ClaimPursuit(
        org_id=org_id,
        external_reference=None,
        status="RECOMMENDED",
        currency="USD",
        declared_minor=amount_minor,
    )
    session.add(pursuit)
    session.flush()
    session.add(
        PursuitAllocation(
            org_id=org_id,
            pursuit_id=pursuit.id,
            obligation_id=obligation_id,
            allocated_minor=amount_minor,
        )
    )


@pytest.mark.parametrize(
    ("settlement_minor", "pursuit_minor", "expected", "recoverable_minor"),
    (
        (100, 0, "SYNTHETIC_CLAIM_READY", 100),
        (0, 100, "SYNTHETIC_CLAIM_READY", 100),
        (200, 0, "RESOLVED", None),
        (0, 200, "ALREADY_PURSUED", None),
    ),
)
def test_explicitly_reconciled_allocations_drive_residual_outcomes(
    runtime_factory,
    worker_factory,
    settings,
    settlement_minor: int,
    pursuit_minor: int,
    expected: str,
    recoverable_minor: int | None,
) -> None:
    org_id = f"org_reconciliation_outcome_{settlement_minor}_{pursuit_minor}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            settlement_reconciliation_state="RECONCILED_COMPLETE",
            pursuit_reconciliation_state="RECONCILED_COMPLETE",
        )
        if settlement_minor:
            _add_settlement(session, org_id, obligation_id, settlement_minor)
        if pursuit_minor:
            _add_active_pursuit(session, org_id, obligation_id, pursuit_minor)
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
    assert assessment.conclusion == expected
    assert assessment.recoverable_minor == recoverable_minor


@pytest.mark.parametrize("domain", ("SETTLEMENT", "PURSUIT"))
def test_reconciled_none_cannot_override_actual_economic_records(
    runtime_factory, worker_factory, settings, domain: str
) -> None:
    org_id = f"org_reconciliation_none_conflict_{domain.lower()}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
        if domain == "SETTLEMENT":
            _add_settlement(session, org_id, obligation_id, 100)
        else:
            _add_active_pursuit(session, org_id, obligation_id, 100)
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


def test_reconciliation_state_change_stales_current_assessment(
    runtime_factory, worker_factory, settings
) -> None:
    # This test mutates certainty deliberately; a per-run tenant avoids
    # carrying that state into a later focused/full test invocation.
    org_id = f"org_reconciliation_state_freshness_{uuid4().hex}"
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
    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.execute(
            update(ReconciliationState)
            .where(
                ReconciliationState.org_id == org_id,
                ReconciliationState.obligation_id == obligation_id,
                ReconciliationState.domain == "SETTLEMENT",
            )
            .values(state="UNKNOWN")
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert current_assessment(session, org_id, obligation_id).state == "STALE"  # type: ignore[union-attr]


def test_reconciliation_without_a_cutoff_is_not_completeness(runtime_factory, worker_factory, settings) -> None:
    org_id = "org_reconciliation_missing_cutoff"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.execute(
            update(ReconciliationState)
            .where(
                ReconciliationState.org_id == org_id,
                ReconciliationState.obligation_id == obligation_id,
                ReconciliationState.domain == "PURSUIT",
            )
            .values(cutoff=None)
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


def test_same_value_in_another_field_cannot_satisfy_synthetic_fee_premise(
    runtime_factory, worker_factory, settings
) -> None:
    """A true value is not proof unless it comes from the required premise field."""
    org_id = "org_proof_wrong_semantic_field"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            fixture_payload={"fee": {"valid": True, "other": True}},
            fact_path="fee.other",
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


def test_supporting_and_contradicting_evidence_for_the_same_premise_conflict(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_proof_supports_and_contradicts"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
        evidence = session.execute(
            select(EvidenceRecord).where(EvidenceRecord.org_id == org_id)
        ).scalar_one()
        session.add(
            EvidenceAssertion(
                org_id=org_id,
                evidence_record_id=evidence.id,
                source_record_version_id=evidence.source_record_version_id,
                proposition_key="synthetic-invalid-fee",
                subject_key="SYN-FEE-001",
                polarity="CONTRADICTS",
                fact_path="fee.valid",
                asserted_value=True,
                scope={"coverage": "KNOWN", "premise_key": "SYNTHETIC_VALID_FEE"},
                decisive=True,
            )
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


def test_revoked_required_evidence_cannot_support_synthetic_readiness(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_proof_revoked"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
        assertion = session.execute(
            select(EvidenceAssertion).where(EvidenceAssertion.org_id == org_id)
        ).scalar_one()
        session.add(
            EvidenceLifecycleEvent(
                org_id=org_id,
                assertion_id=assertion.id,
                state="REVOKED",
                reason="repair-06 regression",
            )
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


def test_duplicate_one_unit_assertions_do_not_satisfy_two_unit_requirement(
    runtime_factory, worker_factory, settings
) -> None:
    """Coverage is checked against one exact evidence record; assertions are not summed."""
    org_id = "org_proof_duplicate_coverage"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            evidence_quantity=1,
            required_quantity="2",
        )
        original = session.execute(
            select(EvidenceAssertion).where(EvidenceAssertion.org_id == org_id)
        ).scalar_one()
        session.add(
            EvidenceAssertion(
                org_id=org_id,
                evidence_record_id=original.evidence_record_id,
                source_record_version_id=original.source_record_version_id,
                proposition_key=original.proposition_key,
                subject_key=original.subject_key,
                polarity=original.polarity,
                fact_path=original.fact_path,
                asserted_value=original.asserted_value,
                scope=original.scope,
                decisive=True,
            )
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


def test_exact_numeric_coverage_can_satisfy_required_quantity(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_proof_sufficient_coverage"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            evidence_quantity=2,
            required_quantity="2",
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


def test_copied_authority_strings_do_not_authorize_altered_fixture_content(
    runtime_factory, worker_factory, settings
) -> None:
    """A-005 reproduction: source-basis strings currently are not bound to fixture bytes."""
    org_id = "org_authority_altered_fixture"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session,
            settings,
            org_id=org_id,
            fixture_payload={"fee": {"valid": True, "altered": "attacker-controlled"}},
            registered_fixture_payload={"fee": {"valid": True}},
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


def test_same_digest_does_not_transfer_synthetic_authority_across_tenants(runtime_factory, worker_factory, settings) -> None:
    payload = {"fee": {"valid": True}}
    with runtime_factory() as session, session.begin():
        _assess_synthetic_candidate(session, settings, org_id="org_authority_alpha", fixture_payload=payload)
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id="org_authority_bravo", fixture_payload=payload, register_profile=False)
    with worker_factory() as session, session.begin():
        set_local_tenant(session, "org_authority_bravo")
        assert assess_synthetic(session, "org_authority_bravo", obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True).conclusion == "REVIEW"


@pytest.mark.parametrize(("stored_minor", "expected"), ((0, "REVIEW"), (100, "REVIEW"), (200, "SYNTHETIC_CLAIM_READY"), (300, "REVIEW")))
def test_stored_entitlement_must_equal_deterministic_event_minus_pinned_rule(runtime_factory, worker_factory, settings, stored_minor: int, expected: str) -> None:
    org_id = f"org_entitlement_{stored_minor}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id, entitlement_minor=stored_minor)
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True)
        assert assessment.conclusion == expected
        if expected == "SYNTHETIC_CLAIM_READY":
            assert assessment.recoverable_minor == 200


@pytest.mark.parametrize(
    "kwargs",
    (
        {"event_direction": "CREDIT", "entitlement_minor": 200},
        {"derivation_currency": "EUR", "entitlement_minor": 200},
        {"event_amount_minor": 800, "entitlement_minor": 0, "expected": "RESOLVED"},
    ),
)
def test_non_debit_currency_and_zero_synthetic_derivations_are_not_claim_ready(runtime_factory, worker_factory, settings, kwargs: dict[str, object]) -> None:
    expected = kwargs.pop("expected", "REVIEW")
    org_id = f"org_derivation_guard_{len(kwargs)}_{kwargs.get('event_amount_minor', 1000)}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id, **kwargs)
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert assess_synthetic(session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True).conclusion == expected


def test_negative_entitlement_is_rejected_by_database(runtime_factory, settings) -> None:
    """Frozen semantics: negative recovery cannot be stored or made positive by assessment."""
    with runtime_factory() as session, session.begin():
        with pytest.raises(DBAPIError):
            _assess_synthetic_candidate(session, settings, org_id="org_negative_entitlement", event_amount_minor=700, entitlement_minor=-100)


@pytest.mark.parametrize("lifecycle", ("SUPERSEDED", "REVOKED"))
def test_pinned_non_active_synthetic_policy_cannot_publish_new_ready_assessment(
    runtime_factory, worker_factory, settings, lifecycle: str
) -> None:
    org_id = f"org_policy_{lifecycle.lower()}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.execute(
            update(PolicySourceVersion)
            .where(PolicySourceVersion.org_id == org_id)
            .values(lifecycle_state=lifecycle)
        )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert assess_synthetic(session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True).conclusion == "REVIEW"


def test_profile_pins_exact_policy_version_despite_duplicate_logical_key(runtime_factory, worker_factory, settings) -> None:
    org_id = "org_policy_version_pin"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.add(PolicySourceVersion(org_id=org_id, policy_key="SYN-VALID-FEE-8", authority_class="SYNTHETIC", content_sha256="f" * 64, effective_from=None, effective_to=None, applicability={"permitted_amount_minor": 1}, raw_text="untrusted duplicate logical key", lifecycle_state="ACTIVE"))
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True)
        assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
        assert assessment.recoverable_minor == 200


def test_runtime_roles_cannot_mutate_trusted_fixture_registry(runtime_factory, worker_factory, settings) -> None:
    org_id = "org_registry_runtime_denial"
    with runtime_factory() as session, session.begin():
        _assess_synthetic_candidate(session, settings, org_id=org_id)
        set_local_tenant(session, org_id)
        profile = session.execute(select(SyntheticFixtureProfile)).scalar_one()
        for operation in (
            lambda: session.add(SyntheticFixtureProfile(org_id=org_id, fixture_profile="forged", fixture_sha256="0" * 64, policy_source_version_id=profile.policy_source_version_id)),
            lambda: session.execute(update(SyntheticFixtureProfile).values(fixture_sha256="1" * 64)),
            lambda: session.execute(SyntheticFixtureProfile.__table__.delete()),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    operation()
                    session.flush()
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        for operation in (
            lambda: session.add(SyntheticFixtureProfile(org_id=org_id, fixture_profile="worker-forged", fixture_sha256="2" * 64, policy_source_version_id=uuid4())),
            lambda: session.execute(update(SyntheticFixtureProfile).values(fixture_sha256="2" * 64)),
            lambda: session.execute(SyntheticFixtureProfile.__table__.delete()),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    operation()
                    session.flush()


def test_pinned_policy_never_falls_back_to_another_active_same_key(runtime_factory, worker_factory, settings) -> None:
    org_id = "org_policy_no_fallback"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        session.add(PolicySourceVersion(org_id=org_id, policy_key="SYN-VALID-FEE-8", authority_class="SYNTHETIC", content_sha256="9" * 64, effective_from=None, effective_to=None, applicability={}, raw_text="alternate active", lifecycle_state="ACTIVE"))
        session.execute(update(PolicySourceVersion).where(PolicySourceVersion.org_id == org_id, PolicySourceVersion.content_sha256 != "9" * 64).values(lifecycle_state="REVOKED"))
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert assess_synthetic(session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True).conclusion == "REVIEW"


@pytest.mark.parametrize(("offset", "expected"), ((timedelta(days=1), "REVIEW"), (timedelta(0), "SYNTHETIC_CLAIM_READY"), (timedelta(days=-1), "REVIEW")))
def test_pinned_policy_effective_period_controls_new_assessments(runtime_factory, worker_factory, settings, offset: timedelta, expected: str) -> None:
    org_id = f"org_policy_period_{offset.days}"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    owner_factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    now = datetime.now(UTC)
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        if offset > timedelta(0):
            session.execute(update(PolicySourceVersion).where(PolicySourceVersion.org_id == org_id).values(effective_from=now + offset, effective_to=None))
        elif offset < timedelta(0):
            session.execute(update(PolicySourceVersion).where(PolicySourceVersion.org_id == org_id).values(effective_from=None, effective_to=now + offset))
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert assess_synthetic(session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True).conclusion == expected


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
