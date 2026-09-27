from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ValidationStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"


class ExecutionState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    COMPLETED = "COMPLETED"


class TenantState(Base):
    __tablename__ = "tenant_state"
    __table_args__ = (
        CheckConstraint("btrim(org_id) <> ''", name="ck_tenant_state_org_nonempty"),
        CheckConstraint("decision_revision >= 0", name="ck_tenant_revision_nonnegative"),
    )

    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RawEnvelope(Base):
    __tablename__ = "raw_envelope"
    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_raw_envelope_org_idempotency"),
        UniqueConstraint("org_id", "id", name="uq_raw_envelope_org_id"),
        CheckConstraint("octet_length(raw_bytes) > 0", name="ck_raw_envelope_nonempty"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_raw_envelope_org_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    input_format: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    declared_source_orgs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    fixture_provenance: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkIntent(Base):
    __tablename__ = "work_intent"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "envelope_id"],
            ["raw_envelope.org_id", "raw_envelope.id"],
            name="fk_work_envelope_tenant",
        ),
        UniqueConstraint("org_id", "envelope_id", name="uq_work_envelope_tenant"),
        UniqueConstraint("org_id", "id", name="uq_work_intent_org_id"),
        CheckConstraint("attempt_count >= 0", name="ck_work_attempt_nonnegative"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_work_intent_org_nonempty"),
        CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'RETRYABLE_FAILURE', 'TERMINAL_FAILURE', 'COMPLETED')",
            name="ck_work_state_known",
        ),
        CheckConstraint(
            "(state = 'RUNNING' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'RUNNING' AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_work_lease_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    envelope_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExecutionState.QUEUED.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkAttempt(Base):
    __tablename__ = "work_attempt"
    __table_args__ = (
        CheckConstraint("btrim(org_id) <> ''", name="ck_work_attempt_org_nonempty"),
        ForeignKeyConstraint(
            ["org_id", "work_intent_id"],
            ["work_intent.org_id", "work_intent.id"],
            name="fk_attempt_work_tenant",
        ),
        UniqueConstraint(
            "org_id", "work_intent_id", "attempt_number", name="uq_attempt_number_tenant"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    work_intent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_token: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint("event_type <> ''", name="ck_audit_event_nonempty_type"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_audit_event_org_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceRecordVersion(Base):
    __tablename__ = "source_record_version"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "raw_envelope_id"],
            ["raw_envelope.org_id", "raw_envelope.id"],
            name="fk_source_record_raw_envelope_tenant",
        ),
        UniqueConstraint(
            "org_id",
            "source_kind",
            "source_record_id",
            "content_sha256",
            name="uq_source_record_version_content",
        ),
        UniqueConstraint("org_id", "id", name="uq_source_record_version_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_source_record_version_org_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(256), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_envelope_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    declared_org_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FinancialEvent(Base):
    __tablename__ = "financial_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "source_record_version_id"],
            ["source_record_version.org_id", "source_record_version.id"],
            name="fk_financial_event_source_version_tenant",
        ),
        UniqueConstraint(
            "org_id", "source_record_version_id", name="uq_financial_event_source_version"
        ),
        UniqueConstraint("org_id", "id", name="uq_financial_event_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_financial_event_org_nonempty"),
        CheckConstraint(
            "direction IN ('DEBIT', 'CREDIT', 'ADJUSTMENT')", name="ck_financial_event_direction"
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_financial_event_currency"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_financial_event_quantity"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_record_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    posting_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posting_time_precision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    incident_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    incident_time_precision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    business_references: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    normalized_fields: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class EvidenceRecord(Base):
    __tablename__ = "evidence_record"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "source_record_version_id"],
            ["source_record_version.org_id", "source_record_version.id"],
            name="fk_evidence_record_source_version_tenant",
        ),
        UniqueConstraint(
            "org_id", "source_record_version_id", name="uq_evidence_record_source_version"
        ),
        UniqueConstraint("org_id", "id", name="uq_evidence_record_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_evidence_record_org_nonempty"),
        CheckConstraint(
            "coverage_quantity IS NULL OR coverage_quantity >= 0",
            name="ck_evidence_record_coverage_quantity",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_record_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_time_precision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    coverage_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    coverage_scope: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    normalized_fields: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class EconomicObligation(Base):
    __tablename__ = "economic_obligation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "financial_event_id"],
            ["financial_event.org_id", "financial_event.id"],
            name="fk_obligation_financial_event_tenant",
        ),
        UniqueConstraint("org_id", "economic_key", name="uq_obligation_economic_key"),
        Index(
            "uq_obligation_financial_basis",
            "org_id",
            "financial_event_id",
            "recovery_basis",
            unique=True,
            postgresql_where=text("financial_event_id IS NOT NULL"),
        ),
        UniqueConstraint("org_id", "id", name="uq_obligation_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_obligation_org_nonempty"),
        CheckConstraint(
            "recovery_basis IN ('INVALID_FEE', 'ELIGIBLE_LOSS_DAMAGE', 'DUPLICATE_BILLING', "
            "'UNDER_REIMBURSEMENT', 'OTHER_SUPPORTED', 'UNKNOWN')",
            name="ck_obligation_recovery_basis",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_obligation_currency"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    economic_key: Mapped[str] = mapped_column(String(256), nullable=False)
    financial_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    recovery_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    business_instance: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    quantity_scope: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AmountDerivation(Base):
    __tablename__ = "amount_derivation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_amount_derivation_obligation_tenant",
        ),
        UniqueConstraint(
            "org_id", "obligation_id", "derivation_version", name="uq_amount_derivation_version"
        ),
        CheckConstraint("btrim(org_id) <> ''", name="ck_amount_derivation_org_nonempty"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_amount_derivation_currency"),
        CheckConstraint(
            "observed_amount_minor IS NULL OR observed_amount_minor >= 0",
            name="ck_amount_derivation_observed_nonnegative",
        ),
        CheckConstraint(
            "expected_amount_minor IS NULL OR expected_amount_minor >= 0",
            name="ck_amount_derivation_expected_nonnegative",
        ),
        CheckConstraint(
            "justified_entitlement_minor IS NULL OR justified_entitlement_minor >= 0",
            name="ck_amount_derivation_entitlement_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    obligation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    derivation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    justified_entitlement_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rounding_rule: Mapped[str] = mapped_column(String(128), nullable=False)
    basis_class: Mapped[str] = mapped_column(String(32), nullable=False)
    source_basis: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReconciliationState(Base):
    __tablename__ = "reconciliation_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_reconciliation_obligation_tenant",
        ),
        UniqueConstraint("org_id", "obligation_id", "domain", name="uq_reconciliation_domain"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    obligation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SettlementAllocation(Base):
    __tablename__ = "settlement_allocation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "credit_event_id"],
            ["financial_event.org_id", "financial_event.id"],
            name="fk_settlement_credit_event_tenant",
        ),
        ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_settlement_obligation_tenant",
        ),
        UniqueConstraint("org_id", "id", name="uq_settlement_allocation_org_id"),
        CheckConstraint("allocated_minor > 0", name="ck_settlement_allocation_positive"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    credit_event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    allocated_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SettlementReversal(Base):
    __tablename__ = "settlement_reversal"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "allocation_id"],
            ["settlement_allocation.org_id", "settlement_allocation.id"],
            name="fk_settlement_reversal_allocation_tenant",
        ),
        CheckConstraint("reversed_minor > 0", name="ck_settlement_reversal_positive"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    allocation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reversed_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ClaimPursuit(Base):
    __tablename__ = "claim_pursuit"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_claim_pursuit_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_claim_pursuit_org_nonempty"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_claim_pursuit_currency"),
        CheckConstraint("declared_minor > 0", name="ck_claim_pursuit_positive"),
        CheckConstraint(
            "status IN ('RECOMMENDED', 'EXPORTED', 'SUBMITTED', 'PENDING', 'RESOLVED', 'REJECTED', 'WITHDRAWN')",
            name="ck_claim_pursuit_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    declared_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PursuitAllocation(Base):
    __tablename__ = "pursuit_allocation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "pursuit_id"],
            ["claim_pursuit.org_id", "claim_pursuit.id"],
            name="fk_pursuit_allocation_pursuit_tenant",
        ),
        ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_pursuit_allocation_obligation_tenant",
        ),
        CheckConstraint("allocated_minor > 0", name="ck_pursuit_allocation_positive"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_pursuit_allocation_org_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pursuit_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    allocated_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceAssertion(Base):
    """A mechanically checkable statement about a source field, not an AI interpretation."""

    __tablename__ = "evidence_assertion"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "evidence_record_id"],
            ["evidence_record.org_id", "evidence_record.id"],
            name="fk_assertion_evidence_tenant",
        ),
        ForeignKeyConstraint(
            ["org_id", "source_record_version_id"],
            ["source_record_version.org_id", "source_record_version.id"],
            name="fk_assertion_source_tenant",
        ),
        UniqueConstraint("org_id", "id", name="uq_evidence_assertion_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_evidence_assertion_org_nonempty"),
        CheckConstraint(
            "polarity IN ('SUPPORTS', 'CONTRADICTS')", name="ck_evidence_assertion_polarity"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence_record_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_record_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    proposition_key: Mapped[str] = mapped_column(String(256), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(256), nullable=False)
    polarity: Mapped[str] = mapped_column(String(16), nullable=False)
    fact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    asserted_value: Mapped[object] = mapped_column(JSONB, nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    decisive: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceLifecycleEvent(Base):
    __tablename__ = "evidence_lifecycle_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "assertion_id"],
            ["evidence_assertion.org_id", "evidence_assertion.id"],
            name="fk_evidence_lifecycle_assertion_tenant",
        ),
        CheckConstraint("btrim(org_id) <> ''", name="ck_evidence_lifecycle_org_nonempty"),
        CheckConstraint(
            "state IN ('AVAILABLE', 'REVOKED', 'SUPERSEDED')", name="ck_evidence_lifecycle_state"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    assertion_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PolicySourceVersion(Base):
    __tablename__ = "policy_source_version"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "policy_key", "content_sha256", name="uq_policy_source_version_content"
        ),
        UniqueConstraint("org_id", "id", name="uq_policy_source_version_org_id"),
        CheckConstraint("btrim(org_id) <> ''", name="ck_policy_source_org_nonempty"),
        CheckConstraint(
            "authority_class IN ('OFFICIAL', 'SYNTHETIC', 'UNVERIFIED')",
            name="ck_policy_source_authority",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_key: Mapped[str] = mapped_column(String(256), nullable=False)
    authority_class: Mapped[str] = mapped_column(String(16), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applicability: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SyntheticFixtureProfile(Base):
    __tablename__ = "synthetic_fixture_profile"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "policy_source_version_id"],
            ["policy_source_version.org_id", "policy_source_version.id"],
            name="fk_synthetic_profile_policy_tenant",
        ),
        UniqueConstraint("org_id", "fixture_sha256", name="uq_synthetic_profile_hash_tenant"),
    )

    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fixture_profile: Mapped[str] = mapped_column(String(128), primary_key=True)
    fixture_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_source_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)


class RecoveryAssessment(Base):
    __tablename__ = "recovery_assessment"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_assessment_obligation_tenant",
        ),
        UniqueConstraint("org_id", "id", name="uq_recovery_assessment_org_id"),
        CheckConstraint(
            "conclusion IN ('REVIEW', 'NO_CLAIM', 'RESOLVED', 'ALREADY_PURSUED', 'SYNTHETIC_CLAIM_READY')",
            name="ck_assessment_conclusion",
        ),
        CheckConstraint(
            "recoverable_minor IS NULL OR recoverable_minor > 0",
            name="ck_assessment_amount_positive",
        ),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    obligation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    conclusion: Mapped[str] = mapped_column(String(32), nullable=False)
    recoverable_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    dependency_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SyntheticPacketReservation(Base):
    __tablename__ = "synthetic_packet_reservation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "assessment_id"],
            ["recovery_assessment.org_id", "recovery_assessment.id"],
            name="fk_packet_assessment_tenant",
        ),
        ForeignKeyConstraint(
            ["org_id", "pursuit_id"],
            ["claim_pursuit.org_id", "claim_pursuit.id"],
            name="fk_packet_pursuit_tenant",
        ),
        UniqueConstraint("org_id", "assessment_id", name="uq_packet_assessment_tenant"),
        UniqueConstraint("org_id", "idempotency_key", name="uq_packet_org_idempotency"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    assessment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    pursuit_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    packet: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CurrentRecoveryRecommendation(Base):
    __tablename__ = "current_recovery_recommendation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_current_obligation_tenant",
        ),
        ForeignKeyConstraint(
            ["org_id", "assessment_id"],
            ["recovery_assessment.org_id", "recovery_assessment.id"],
            name="fk_current_assessment_tenant",
        ),
        UniqueConstraint("org_id", "assessment_id", name="uq_current_assessment_tenant"),
    )
    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    obligation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    assessment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
