"""Synthetic-only deterministic assessment; it never files or exports an external claim."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from recovery_manager.evidence import discover_assertions, prove_assertion
from recovery_manager.ledger import (
    LedgerContributorProof,
    Residual,
    ledger_contributor_proof,
    residual_for_obligation,
)
from recovery_manager.models import (
    AmountDerivation,
    ClaimPursuit,
    CurrentRecoveryRecommendation,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceRecord,
    FinancialEvent,
    PolicySourceVersion,
    PursuitAllocation,
    ReconciliationState,
    RecoveryAssessment,
    SourceRecordVersion,
    SyntheticFixtureProfile,
    SyntheticPacketReservation,
    TenantState,
)


@dataclass(frozen=True)
class CurrentAssessmentView:
    assessment: RecoveryAssessment
    state: str


def _matches_pinned_synthetic_derivation(
    obligation: EconomicObligation,
    derivation: AmountDerivation | None,
    financial_event: FinancialEvent | None,
    policy: PolicySourceVersion | None,
) -> bool:
    """Validate every numeric input to the synthetic $observed - $permitted rule."""
    permitted = policy.applicability.get("permitted_amount_minor") if policy else None
    if (
        derivation is None
        or financial_event is None
        or financial_event.direction != "DEBIT"
        or not isinstance(permitted, int)
        or permitted < 0
        or financial_event.currency != obligation.currency
        or derivation.currency != obligation.currency
        or derivation.observed_amount_minor != financial_event.amount_minor
        or derivation.expected_amount_minor != permitted
        or financial_event.amount_minor < permitted
    ):
        return False
    return derivation.justified_entitlement_minor == financial_event.amount_minor - permitted


def _supports_required_synthetic_premises(
    session: Session,
    org_id: str,
    obligation: EconomicObligation,
    assertions: tuple[EvidenceAssertion, ...],
) -> bool:
    """Require a supporting, subject-bound fact with enough exact evidence coverage."""
    if obligation.quantity_scope.get("coverage") != "KNOWN":
        return False
    try:
        required_quantity = Decimal(str(obligation.quantity_scope["quantity"]))
    except (InvalidOperation, KeyError, TypeError):
        return False
    if required_quantity <= 0:
        return False
    for assertion in assertions:
        if (
            assertion.polarity != "SUPPORTS"
            or assertion.scope.get("premise_key") != "SYNTHETIC_VALID_FEE"
            or assertion.subject_key != obligation.economic_key
            # The supported synthetic rule has one explicit premise: the
            # source-backed ``fee.valid`` flag must be affirmatively true.
            # A caller-labelled premise or an equally true neighbouring field
            # is not interchangeable proof.
            or assertion.fact_path != "fee.valid"
            or assertion.asserted_value is not True
        ):
            continue
        evidence = session.execute(
            select(EvidenceRecord).where(
                EvidenceRecord.org_id == org_id,
                EvidenceRecord.id == assertion.evidence_record_id,
            )
        ).scalar_one_or_none()
        if (
            evidence is not None
            and evidence.coverage_scope.get("coverage") == "KNOWN"
            and evidence.coverage_quantity is not None
            and evidence.coverage_quantity >= required_quantity
        ):
            return True
    return False


def derived_recommendation(residual: Residual, prerequisites: bool) -> tuple[str, int | None]:
    """Derive a recommendation from known ledger state; never clamp conflicts."""
    if not prerequisites or residual.justified_entitlement_minor is None:
        return "REVIEW", None
    entitlement = residual.justified_entitlement_minor
    if residual.allocated_settlement_minor > entitlement:
        return "REVIEW", None
    if residual.allocated_settlement_minor == entitlement:
        return "RESOLVED", None
    if residual.active_pursuit_minor > entitlement - residual.allocated_settlement_minor:
        return "REVIEW", None
    if residual.active_pursuit_minor == entitlement - residual.allocated_settlement_minor:
        return "ALREADY_PURSUED", None
    if residual.remaining_minor is None or residual.remaining_minor <= 0:
        return "REVIEW", None
    return "SYNTHETIC_CLAIM_READY", residual.remaining_minor


def current_assessment(
    session: Session, org_id: str, obligation_id: UUID
) -> CurrentAssessmentView | None:
    """Read the current pointer without presenting a stale result as actionable."""
    pointer = session.execute(
        select(CurrentRecoveryRecommendation).where(
            CurrentRecoveryRecommendation.org_id == org_id,
            CurrentRecoveryRecommendation.obligation_id == obligation_id,
        )
    ).scalar_one_or_none()
    if pointer is None:
        return None
    assessment = session.execute(
        select(RecoveryAssessment).where(
            RecoveryAssessment.org_id == org_id,
            RecoveryAssessment.id == pointer.assessment_id,
        )
    ).scalar_one()
    revision = session.execute(
        select(TenantState.decision_revision).where(TenantState.org_id == org_id)
    ).scalar_one()
    actionable = assessment.conclusion != "SYNTHETIC_CLAIM_READY" or _snapshot_policy_is_actionable(
        session, org_id, assessment.dependency_snapshot, datetime.now(UTC)
    )
    return CurrentAssessmentView(
        assessment=assessment,
        state="CURRENT" if assessment.tenant_revision == revision and actionable else "STALE",
    )


def _snapshot_policy_is_actionable(
    session: Session, org_id: str, snapshot: dict[str, object], as_of: datetime
) -> bool:
    """Fail closed unless the exact policy pinned at assessment time remains usable."""
    policy_id = snapshot.get("policy_source_version_id")
    if not isinstance(policy_id, str):
        return False
    try:
        policy_uuid = UUID(policy_id)
    except ValueError:
        return False
    policy = session.execute(
        select(PolicySourceVersion).where(
            PolicySourceVersion.org_id == org_id,
            PolicySourceVersion.id == policy_uuid,
            PolicySourceVersion.authority_class == "SYNTHETIC",
            PolicySourceVersion.lifecycle_state == "ACTIVE",
        )
    ).scalar_one_or_none()
    return (
        policy is not None
        and (policy.effective_from is None or policy.effective_from <= as_of)
        and (policy.effective_to is None or policy.effective_to >= as_of)
    )


def assess_synthetic(
    session: Session,
    org_id: str,
    obligation_id: UUID,
    subject_key: str,
    proposition_key: str,
    *,
    synthetic_capability_enabled: bool = False,
) -> RecoveryAssessment:
    as_of = datetime.now(UTC)
    revision = int(
        session.execute(text("SELECT public.lock_current_tenant_revision()")).scalar_one()
    )
    obligation = session.execute(
        select(EconomicObligation).where(
            EconomicObligation.org_id == org_id, EconomicObligation.id == obligation_id
        )
    ).scalar_one()
    derivation = session.execute(
        select(AmountDerivation)
        .where(AmountDerivation.org_id == org_id, AmountDerivation.obligation_id == obligation_id)
        .order_by(AmountDerivation.derivation_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    financial_event = (
        session.execute(
            select(FinancialEvent).where(
                FinancialEvent.org_id == org_id,
                FinancialEvent.id == obligation.financial_event_id,
            )
        ).scalar_one_or_none()
        if obligation.financial_event_id is not None
        else None
    )
    fixture_source = (
        session.execute(
            select(SourceRecordVersion).where(
                SourceRecordVersion.org_id == org_id,
                SourceRecordVersion.id == financial_event.source_record_version_id,
            )
        ).scalar_one_or_none()
        if financial_event is not None
        else None
    )
    found = discover_assertions(session, org_id, subject_key, proposition_key, limit=100)
    residual = residual_for_obligation(session, org_id, obligation_id)
    reconciliation: dict[str, tuple[UUID, str, datetime | None, str]] = {}
    for reconciliation_id, domain, state, cutoff, source_set_sha256 in session.execute(
        select(
            ReconciliationState.id,
            ReconciliationState.domain,
            ReconciliationState.state,
            ReconciliationState.cutoff,
            ReconciliationState.source_set_sha256,
        ).where(
            ReconciliationState.org_id == org_id,
            ReconciliationState.obligation_id == obligation_id,
        )
    ).tuples():
        reconciliation[domain] = (reconciliation_id, state, cutoff, source_set_sha256)
    def complete_reconciliation(domain: str) -> bool:
        state_and_cutoff = reconciliation.get(domain)
        if state_and_cutoff is None:
            return False
        _, state, cutoff, _ = state_and_cutoff
        return (
            state in {"RECONCILED_NONE", "RECONCILED_COMPLETE"}
            and cutoff is not None
            and cutoff <= as_of
        )

    reconciliation_complete = all(
        complete_reconciliation(domain) for domain in ("SETTLEMENT", "PURSUIT")
    )
    reconciliation_consistent = not (
        reconciliation.get("SETTLEMENT", (None, "UNKNOWN", None, ""))[1] == "RECONCILED_NONE"
        and residual.allocated_settlement_minor != 0
    ) and not (
        reconciliation.get("PURSUIT", (None, "UNKNOWN", None, ""))[1] == "RECONCILED_NONE"
        and residual.active_pursuit_minor != 0
    )
    reconciliation_snapshot = {
        domain: {
            "id": str(reconciliation_id),
            "state": state,
            "cutoff": cutoff.isoformat() if cutoff else None,
            "source_set_sha256": source_set_sha256,
        }
        for domain, (reconciliation_id, state, cutoff, source_set_sha256) in reconciliation.items()
    }
    ledger_proof: LedgerContributorProof = ledger_contributor_proof(
        session,
        org_id,
        obligation_id,
        residual.currency,
        expected_settlement_minor=residual.allocated_settlement_minor,
        expected_active_pursuit_minor=residual.active_pursuit_minor,
        reconciliation=reconciliation_snapshot,
    )
    source_basis = derivation.source_basis if derivation is not None else {}
    fixture_profile = source_basis.get("fixture_profile")
    fixture_sha256 = source_basis.get("fixture_sha256")
    profile = (
        session.execute(
            select(SyntheticFixtureProfile).where(
                SyntheticFixtureProfile.org_id == org_id,
                SyntheticFixtureProfile.fixture_profile == fixture_profile,
                SyntheticFixtureProfile.fixture_sha256 == fixture_sha256,
            )
        ).scalar_one_or_none()
        if isinstance(fixture_profile, str) and isinstance(fixture_sha256, str)
        else None
    )
    policy = (
        session.execute(
            select(PolicySourceVersion).where(
                PolicySourceVersion.org_id == org_id,
                PolicySourceVersion.id == profile.policy_source_version_id,
                PolicySourceVersion.authority_class == "SYNTHETIC",
                PolicySourceVersion.lifecycle_state == "ACTIVE",
            )
        ).scalar_one_or_none()
        if profile is not None
        else None
    )
    policy_applies = (
        policy is not None
        and profile is not None
        and fixture_source is not None
        and fixture_source.content_sha256 == profile.fixture_sha256
        and policy.applicability.get("fixture_profile") == fixture_profile
        and policy.applicability.get("fixture_sha256") == fixture_sha256
        and policy.applicability.get("proposition_key") == proposition_key
        and obligation.business_instance.get("fixture_profile") == fixture_profile
        and obligation.business_instance.get("fixture_sha256") == fixture_sha256
        and (policy.effective_from is None or policy.effective_from <= as_of)
        and (policy.effective_to is None or policy.effective_to >= as_of)
    )
    decisive = tuple(assertion for assertion in found.assertions if assertion.decisive)
    prerequisites = (
        synthetic_capability_enabled
        and derivation is not None
        and derivation.basis_class == "SYNTHETIC_ONLY"
        and obligation.recovery_basis == "INVALID_FEE"
        and residual.remaining_minor is not None
        and reconciliation_complete
        and reconciliation_consistent
        and ledger_proof.consistent
        and found.complete
        and not found.conflict_present
        and policy_applies
        and _matches_pinned_synthetic_derivation(obligation, derivation, financial_event, policy)
        and bool(decisive)
        and all(assertion.scope.get("coverage") == "KNOWN" for assertion in decisive)
        and all(
            prove_assertion(session, org_id, assertion).mechanically_supported
            for assertion in decisive
        )
        and subject_key == obligation.economic_key
        and _supports_required_synthetic_premises(session, org_id, obligation, decisive)
    )
    conclusion, amount = derived_recommendation(residual, prerequisites)
    assessment_id = uuid4()
    snapshot = {
        "tenant_revision": revision,
        "assessment_as_of": as_of.isoformat(),
        "obligation": {
            "id": str(obligation.id),
            "financial_event_id": str(obligation.financial_event_id)
            if obligation.financial_event_id
            else None,
            "economic_key": obligation.economic_key,
            "recovery_basis": obligation.recovery_basis,
            "currency": obligation.currency,
            "quantity_scope": obligation.quantity_scope,
        },
        "financial_event": {
            "id": str(financial_event.id),
            "source_record_version_id": str(financial_event.source_record_version_id),
            "direction": financial_event.direction,
            "amount_minor": financial_event.amount_minor,
            "currency": financial_event.currency,
            "quantity": str(financial_event.quantity) if financial_event.quantity is not None else None,
        }
        if financial_event
        else None,
        "amount_derivation": {
            "id": str(derivation.id),
            "version": derivation.derivation_version,
            "currency": derivation.currency,
            "observed_amount_minor": derivation.observed_amount_minor,
            "expected_amount_minor": derivation.expected_amount_minor,
            "justified_entitlement_minor": derivation.justified_entitlement_minor,
            "source_basis": derivation.source_basis,
        }
        if derivation
        else None,
        "trusted_fixture_profile": {
            "fixture_profile": profile.fixture_profile,
            "fixture_sha256": profile.fixture_sha256,
            "policy_source_version_id": str(profile.policy_source_version_id),
            "actual_source_record_version_id": str(fixture_source.id) if fixture_source else None,
            "actual_source_sha256": fixture_source.content_sha256 if fixture_source else None,
        }
        if profile
        else None,
        "policy_source_version_id": str(policy.id) if policy else None,
        "policy": {
            "id": str(policy.id),
            "policy_key": policy.policy_key,
            "content_sha256": policy.content_sha256,
            "lifecycle_state": policy.lifecycle_state,
            "effective_from": policy.effective_from.isoformat() if policy and policy.effective_from else None,
            "effective_to": policy.effective_to.isoformat() if policy and policy.effective_to else None,
            "applicability": policy.applicability,
        }
        if policy
        else None,
        "evidence": [
            {
                "assertion_id": str(assertion.id),
                "evidence_record_id": str(assertion.evidence_record_id),
                "source_record_version_id": str(assertion.source_record_version_id),
                "proposition_key": assertion.proposition_key,
                "subject_key": assertion.subject_key,
                "polarity": assertion.polarity,
                "fact_path": assertion.fact_path,
                "asserted_value": assertion.asserted_value,
                "scope": assertion.scope,
                "decisive": assertion.decisive,
            }
            for assertion in found.assertions
        ],
        "reconciliation": reconciliation_snapshot,
        "ledger": {
            "justified_entitlement_minor": residual.justified_entitlement_minor,
            "allocated_settlement_minor": residual.allocated_settlement_minor,
            "active_pursuit_minor": residual.active_pursuit_minor,
            "remaining_minor": residual.remaining_minor,
            "currency": residual.currency,
        },
        "ledger_proof": {
            "settlement": ledger_proof.settlement,
            "pursuit": ledger_proof.pursuit,
            "consistent": ledger_proof.consistent,
        },
        "retrieval_complete": found.complete,
    }
    session.execute(
        text(
            "SELECT public.publish_recovery_assessment("
            ":assessment_id, :obligation_id, :revision, :conclusion, :recoverable_minor, :currency, "
            "CAST(:snapshot AS jsonb))"
        ),
        {
            "assessment_id": assessment_id,
            "obligation_id": obligation.id,
            "revision": revision,
            "conclusion": conclusion,
            "recoverable_minor": amount,
            "currency": residual.currency if amount is not None else None,
            "snapshot": json.dumps(snapshot),
        },
    )
    return session.execute(
        select(RecoveryAssessment).where(
            RecoveryAssessment.org_id == org_id, RecoveryAssessment.id == assessment_id
        )
    ).scalar_one()


def reserve_synthetic_packet(
    session: Session, org_id: str, assessment_id: UUID, idempotency_key: str
) -> SyntheticPacketReservation:
    existing = session.execute(
        select(SyntheticPacketReservation).where(
            SyntheticPacketReservation.org_id == org_id,
            SyntheticPacketReservation.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.assessment_id != assessment_id:
            raise ValueError("Export idempotency key conflicts with another assessment")
        return existing
    # The tenant revision lock is the first authoritative lock in both
    # publication and export.  The preliminary idempotency lookup above is
    # only a fast path; it is repeated after this lock before any reservation.
    revision = int(
        session.execute(text("SELECT public.lock_current_tenant_revision()")).scalar_one()
    )
    existing = session.execute(
        select(SyntheticPacketReservation)
        .where(
            SyntheticPacketReservation.org_id == org_id,
            SyntheticPacketReservation.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.assessment_id != assessment_id:
            raise ValueError("Export idempotency key conflicts with another assessment")
        return existing
    assessment = session.execute(
        select(RecoveryAssessment).where(
            RecoveryAssessment.org_id == org_id, RecoveryAssessment.id == assessment_id
        )
    ).scalar_one_or_none()
    if assessment is None:
        raise ValueError("Assessment is not current synthetic claim-ready")
    if assessment.conclusion != "SYNTHETIC_CLAIM_READY" or assessment.tenant_revision != revision:
        raise ValueError("Assessment is not current synthetic claim-ready")
    if not _snapshot_policy_is_actionable(
        session, org_id, assessment.dependency_snapshot, datetime.now(UTC)
    ):
        raise ValueError("Assessment is not current synthetic claim-ready")
    current_assessment_id = session.execute(
        text("SELECT public.lock_current_recovery_assessment(:obligation_id)"),
        {"obligation_id": assessment.obligation_id},
    ).scalar_one_or_none()
    if current_assessment_id != assessment.id:
        raise ValueError("Assessment is historical or stale")
    pursuit = ClaimPursuit(
        org_id=org_id,
        external_reference=None,
        status="EXPORTED",
        currency=assessment.currency,
        declared_minor=assessment.recoverable_minor,
    )
    session.add(pursuit)
    session.flush()
    session.add(
        PursuitAllocation(
            org_id=org_id,
            pursuit_id=pursuit.id,
            obligation_id=assessment.obligation_id,
            allocated_minor=assessment.recoverable_minor,
        )
    )
    session.flush()
    packet = SyntheticPacketReservation(
        org_id=org_id,
        assessment_id=assessment.id,
        idempotency_key=idempotency_key,
        pursuit_id=pursuit.id,
        packet={
            "synthetic_only": True,
            "assessment_id": str(assessment.id),
            "amount_minor": assessment.recoverable_minor,
            "currency": assessment.currency,
            "snapshot": assessment.dependency_snapshot,
        },
    )
    session.add(packet)
    session.flush()
    return packet
