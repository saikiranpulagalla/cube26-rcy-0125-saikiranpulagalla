from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
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
