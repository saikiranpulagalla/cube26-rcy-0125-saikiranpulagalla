"""Controlled synthetic world-state provisioning for demos and benchmarks.

This module creates inputs only. Assessment and publication remain the normal runtime path.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Principal, Settings
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    AmountDerivation,
    ClaimPursuit,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceRecord,
    FinancialEvent,
    PolicySourceVersion,
    PursuitAllocation,
    ReconciliationState,
    SettlementAllocation,
    SourceRecordVersion,
    SyntheticFixtureProfile,
)

BENCHMARK_LOCK_TIMEOUT = "3s"
BENCHMARK_STATEMENT_TIMEOUT = "15s"
_SETUP_FIELDS = frozenset({
    "kind", "financial_event_amount_minor", "financial_event_direction", "currency",
    "permitted_amount_minor", "policy_effective_from", "policy_effective_to", "evidence",
    "evidence_quantity", "required_quantity", "reconciliation", "settlement_reconciliation",
    "pursuit_reconciliation", "settlement_cutoff", "pursuit_cutoff", "settlement_minor",
    "pursuit_minor", "opportunity_id", "obligation_id", "evidence_id",
})
_RECONCILIATION_STATES = frozenset({"RECONCILED_NONE", "RECONCILED_COMPLETE", "UNKNOWN"})


class _OmittedCutoff:
    """Private marker for a cutoff field absent from typed benchmark setup."""


_OMITTED_CUTOFF = _OmittedCutoff()
_Cutoff = datetime | _OmittedCutoff


def configure_benchmark_transaction(session: Session) -> None:
    """Bound benchmark-only work; a timeout is never converted into REVIEW."""
    session.execute(text(f"SET LOCAL lock_timeout = '{BENCHMARK_LOCK_TIMEOUT}'"))
    session.execute(text(f"SET LOCAL statement_timeout = '{BENCHMARK_STATEMENT_TIMEOUT}'"))


def _timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"benchmark {field} must be an ISO-8601 timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"benchmark {field} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"benchmark {field} must include a timezone")
    return parsed.astimezone(UTC)


def _cutoff(raw: Mapping[str, Any], field: str) -> _Cutoff:
    """Parse an optional fixture cutoff without treating explicit null as a default."""
    if field not in raw:
        return _OMITTED_CUTOFF
    value = raw[field]
    if value is None:
        raise ValueError(f"benchmark {field} may be omitted but cannot be null")
    parsed = _timestamp(value, field)
    if parsed is None:
        raise ValueError(f"benchmark {field} may be omitted but cannot be null")
    return parsed


def _minor(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"benchmark {field} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class SyntheticRecoverySetup:
    financial_event_amount_minor: int = 1000
    financial_event_direction: str = "DEBIT"
    currency: str = "USD"
    permitted_amount_minor: int = 800
    evidence: str = "valid"
    evidence_quantity: int = 1
    required_quantity: int = 1
    reconciliation: str | None = None
    settlement_reconciliation: str | None = None
    pursuit_reconciliation: str | None = None
    settlement_cutoff: _Cutoff | None = _OMITTED_CUTOFF
    pursuit_cutoff: _Cutoff | None = _OMITTED_CUTOFF
    settlement_minor: int = 0
    pursuit_minor: int = 0
    policy_available: bool = True
    policy_effective_from: datetime | None = None
    policy_effective_to: datetime | None = None
    logical_opportunity_id: str = "SYNTHETIC-OPPORTUNITY"
    logical_obligation_id: str = "SYNTHETIC-OBLIGATION"
    logical_evidence_id: str = "E-FEE-VALID"

    def __post_init__(self) -> None:
        for field in ("settlement_cutoff", "pursuit_cutoff"):
            value = getattr(self, field)
            if value is None:
                raise ValueError(f"benchmark {field} may be omitted but cannot be null")
            if not isinstance(value, datetime | _OmittedCutoff):
                raise ValueError(f"benchmark {field} must be an ISO-8601 timestamp")

    @classmethod
    def from_manifest(cls, raw: Mapping[str, Any]) -> SyntheticRecoverySetup:
        unknown = set(raw) - _SETUP_FIELDS
        if unknown:
            raise ValueError(f"unsupported benchmark setup fields: {', '.join(sorted(unknown))}")
        kind = raw.get("kind", "synthetic")
        if kind not in {"synthetic", "ordinary"}:
            raise ValueError("benchmark kind must be synthetic or ordinary")
        currency = raw.get("currency", "USD")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
            raise ValueError("benchmark currency must be a three-letter uppercase code")
        direction = raw.get("financial_event_direction", "DEBIT")
        if direction not in {"DEBIT", "CREDIT", "ADJUSTMENT"}:
            raise ValueError("benchmark financial_event_direction is invalid")
        evidence = raw.get("evidence", "valid")
        if evidence not in {"valid", "insufficient"}:
            raise ValueError("benchmark evidence must be valid or insufficient")
        amount = _minor(raw.get("financial_event_amount_minor", 1000), "financial_event_amount_minor")
        permitted = _minor(raw.get("permitted_amount_minor", 800), "permitted_amount_minor")
        if permitted > amount:
            raise ValueError("benchmark permitted amount cannot exceed financial event amount")

        def state(field: str) -> str | None:
            value = raw.get(field)
            if value is not None and value not in _RECONCILIATION_STATES:
                raise ValueError(f"benchmark {field} is invalid")
            return value

        identities = {
            "opportunity_id": raw.get("opportunity_id", "SYNTHETIC-OPPORTUNITY"),
            "obligation_id": raw.get("obligation_id", "SYNTHETIC-OBLIGATION"),
            "evidence_id": raw.get("evidence_id", "E-FEE-VALID"),
        }
        if not all(isinstance(value, str) and value.strip() for value in identities.values()):
            raise ValueError("benchmark logical identities must be non-empty strings")
        return cls(
            financial_event_amount_minor=amount,
            financial_event_direction=direction,
            currency=currency,
            permitted_amount_minor=permitted,
            evidence=evidence,
            evidence_quantity=_minor(raw.get("evidence_quantity", 1), "evidence_quantity"),
            required_quantity=_minor(raw.get("required_quantity", 1), "required_quantity"),
            reconciliation=state("reconciliation"),
            settlement_reconciliation=state("settlement_reconciliation"),
            pursuit_reconciliation=state("pursuit_reconciliation"),
            # Omitted cutoffs use the fixture creation time. Explicit null is
            # rejected above rather than fabricated into reconciliation certainty.
            settlement_cutoff=_cutoff(raw, "settlement_cutoff"),
            pursuit_cutoff=_cutoff(raw, "pursuit_cutoff"),
            settlement_minor=_minor(raw.get("settlement_minor", 0), "settlement_minor"),
            pursuit_minor=_minor(raw.get("pursuit_minor", 0), "pursuit_minor"),
            policy_available=kind == "synthetic",
            policy_effective_from=_timestamp(raw.get("policy_effective_from"), "policy_effective_from"),
            policy_effective_to=_timestamp(raw.get("policy_effective_to"), "policy_effective_to"),
            logical_opportunity_id=str(identities["opportunity_id"]),
            logical_obligation_id=str(identities["obligation_id"]),
            logical_evidence_id=str(identities["evidence_id"]),
        )

    def reconciliation_state(self, domain: str) -> str:
        amount = self.settlement_minor if domain == "SETTLEMENT" else self.pursuit_minor
        explicit = self.settlement_reconciliation if domain == "SETTLEMENT" else self.pursuit_reconciliation
        state = explicit or self.reconciliation or (
            "RECONCILED_COMPLETE" if amount else "RECONCILED_NONE"
        )
        if amount and state != "RECONCILED_COMPLETE":
            raise ValueError(f"benchmark {domain.lower()} allocations require RECONCILED_COMPLETE")
        return state


@dataclass(frozen=True)
class ProvisionedRecoveryCase:
    org_id: str
    logical_opportunities: dict[str, UUID]
    logical_obligations: dict[str, UUID]
    logical_evidence: dict[str, UUID]

    @property
    def obligation_id(self) -> UUID:
        return self.logical_obligations[next(iter(self.logical_obligations))]


def _add_settlement(
    session: Session, org_id: str, obligation_id: UUID, setup: SyntheticRecoverySetup
) -> None:
    if not setup.settlement_minor:
        return
    source = SourceRecordVersion(
        org_id=org_id,
        source_kind="synthetic-settlement",
        source_record_id=f"settlement-{obligation_id}",
        content_sha256=hashlib.sha256(f"settlement-{obligation_id}".encode()).hexdigest(),
        declared_org_id=org_id,
        payload={"credit": {"amount_minor": setup.settlement_minor}},
    )
    session.add(source)
    session.flush()
    credit = FinancialEvent(
        org_id=org_id,
        source_record_version_id=source.id,
        event_type="SYNTHETIC_CREDIT",
        direction="CREDIT",
        amount_minor=setup.settlement_minor,
        currency=setup.currency,
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
            allocated_minor=setup.settlement_minor,
            rationale="benchmark declared settlement",
        )
    )


def _add_active_pursuit(
    session: Session, org_id: str, obligation_id: UUID, setup: SyntheticRecoverySetup
) -> None:
    if not setup.pursuit_minor:
        return
    pursuit = ClaimPursuit(
        org_id=org_id,
        external_reference=None,
        status="RECOMMENDED",
        currency=setup.currency,
        declared_minor=setup.pursuit_minor,
    )
    session.add(pursuit)
    session.flush()
    session.add(
        PursuitAllocation(
            org_id=org_id,
            pursuit_id=pursuit.id,
            obligation_id=obligation_id,
            allocated_minor=setup.pursuit_minor,
        )
    )


def provision_synthetic_recovery_case(
    session: Session, settings: Settings, org_id: str, setup: SyntheticRecoverySetup
) -> ProvisionedRecoveryCase:
    """Provision controlled inputs; never creates an assessment or current pointer."""
    configure_benchmark_transaction(session)
    set_local_tenant(session, org_id)
    payload = {"fee": {"valid": setup.evidence == "valid"}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": digest}
    accept_input(session, Principal(org_id, "benchmark_fixture", "fixture_admin"), b"benchmark fixture", "application/octet-stream", "benchmark", f"benchmark:{org_id}", settings)
    source = SourceRecordVersion(org_id=org_id, source_kind="synthetic", source_record_id="SYN-FEE-001", content_sha256=digest, declared_org_id=org_id, payload=payload)
    session.add(source)
    session.flush()
    event = FinancialEvent(org_id=org_id, source_record_version_id=source.id, event_type="SYNTHETIC_FEE", direction=setup.financial_event_direction, amount_minor=setup.financial_event_amount_minor, currency=setup.currency, quantity=1, posting_time=None, posting_time_precision=None, incident_time=None, incident_time_precision=None, business_references={"synthetic_fixture": "SYN-FEE-001"}, normalized_fields={"synthetic": True})
    evidence = EvidenceRecord(org_id=org_id, source_record_version_id=source.id, evidence_kind="SYNTHETIC", observed_time=None, observed_time_precision=None, coverage_quantity=setup.evidence_quantity, coverage_scope={"coverage": "KNOWN"}, normalized_fields={})
    session.add(event)
    session.flush()
    obligation = EconomicObligation(org_id=org_id, economic_key="SYN-FEE-001", financial_event_id=event.id, recovery_basis="INVALID_FEE", currency=setup.currency, business_instance=provenance, quantity_scope={"coverage": "KNOWN", "quantity": str(setup.required_quantity)})
    session.add_all((evidence, obligation))
    session.flush()
    now = datetime.now(UTC)
    settlement_cutoff = now if setup.settlement_cutoff is _OMITTED_CUTOFF else setup.settlement_cutoff
    pursuit_cutoff = now if setup.pursuit_cutoff is _OMITTED_CUTOFF else setup.pursuit_cutoff
    if not isinstance(settlement_cutoff, datetime) or not isinstance(pursuit_cutoff, datetime):
        raise ValueError("benchmark cutoff must be omitted or a timestamp")
    session.add_all((
        ReconciliationState(org_id=org_id, obligation_id=obligation.id, domain="SETTLEMENT", state=setup.reconciliation_state("SETTLEMENT"), cutoff=settlement_cutoff, source_set_sha256="e" * 64),
        ReconciliationState(org_id=org_id, obligation_id=obligation.id, domain="PURSUIT", state=setup.reconciliation_state("PURSUIT"), cutoff=pursuit_cutoff, source_set_sha256="f" * 64),
    ))
    session.add(EvidenceAssertion(org_id=org_id, evidence_record_id=evidence.id, source_record_version_id=source.id, proposition_key="synthetic-invalid-fee", subject_key="SYN-FEE-001", polarity="SUPPORTS", fact_path="fee.valid", asserted_value=setup.evidence == "valid", scope={"coverage": "KNOWN", "premise_key": "SYNTHETIC_VALID_FEE"}, decisive=True))
    session.add(AmountDerivation(org_id=org_id, obligation_id=obligation.id, derivation_version=1, currency=setup.currency, observed_amount_minor=setup.financial_event_amount_minor, expected_amount_minor=setup.permitted_amount_minor, justified_entitlement_minor=setup.financial_event_amount_minor - setup.permitted_amount_minor, rounding_rule="integer minor units", basis_class="SYNTHETIC_ONLY", source_basis=provenance))
    session.flush()
    _add_settlement(session, org_id, obligation.id, setup)
    _add_active_pursuit(session, org_id, obligation.id, setup)
    session.flush()
    return ProvisionedRecoveryCase(
        org_id,
        {setup.logical_opportunity_id: event.id},
        {setup.logical_obligation_id: obligation.id},
        {setup.logical_evidence_id: evidence.id},
    )


def register_synthetic_authority(settings: Settings, org_id: str, setup: SyntheticRecoverySetup) -> None:
    """Perform owner-only fixture registration before the runtime transaction."""
    if not setup.policy_available:
        return
    payload = {"fee": {"valid": setup.evidence == "valid"}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with factory() as session, session.begin():
        configure_benchmark_transaction(session)
        set_local_tenant(session, org_id)
        policy = PolicySourceVersion(org_id=org_id, policy_key="SYN-VALID-FEE", authority_class="SYNTHETIC", content_sha256="b" * 64, effective_from=setup.policy_effective_from, effective_to=setup.policy_effective_to, applicability={"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": digest, "proposition_key": "synthetic-invalid-fee", "permitted_amount_minor": setup.permitted_amount_minor}, raw_text="SYNTHETIC MECHANICS", lifecycle_state="ACTIVE")
        session.add(policy)
        session.flush()
        session.add(SyntheticFixtureProfile(org_id=org_id, fixture_profile="synthetic-mechanics-v1", fixture_sha256=digest, policy_source_version_id=policy.id))
