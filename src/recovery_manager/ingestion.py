from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recovery_manager.config import Principal, Settings
from recovery_manager.models import (
    AuditEvent,
    RawEnvelope,
    WorkIntent,
)
from recovery_manager.validation import validate_input


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class AcceptanceResult:
    envelope_id: UUID
    work_id: UUID
    replayed: bool
    validation_status: str
    quarantine_reason: str | None


def _advance_tenant_revision(session: Session, org_id: str) -> int:
    return int(
        session.execute(text("SELECT advance_tenant_revision(:org_id)"), {"org_id": org_id}).scalar_one()
    )


def _replay_if_present(
    session: Session, org_id: str, idempotency_key: str, digest: str
) -> AcceptanceResult | None:
    existing = session.execute(
        select(RawEnvelope).where(
            RawEnvelope.org_id == org_id, RawEnvelope.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.sha256 != digest:
        raise IdempotencyConflict("Idempotency-Key was already used with different bytes")
    work = session.execute(
        select(WorkIntent).where(
            WorkIntent.org_id == org_id, WorkIntent.envelope_id == existing.id
        )
    ).scalar_one()
    return AcceptanceResult(
        existing.id, work.id, True, existing.validation_status, existing.quarantine_reason
    )

def accept_input(
    session: Session,
    principal: Principal,
    raw: bytes,
    content_type: str,
    source_name: str,
    idempotency_key: str,
    settings: Settings,
    fixture_provenance: dict[str, object] | None = None,
) -> AcceptanceResult:
    if not idempotency_key.strip():
        raise ValueError("Idempotency-Key must not be empty")
    if len(idempotency_key) > 256:
        raise ValueError("Idempotency-Key is too long")
    result = validate_input(raw, content_type, settings.max_input_bytes, principal.org_id)
    digest = sha256(raw).hexdigest()
    if replay := _replay_if_present(session, principal.org_id, idempotency_key, digest):
        return replay

    try:
        with session.begin_nested():
            envelope = RawEnvelope(
                org_id=principal.org_id,
                actor_id=principal.actor_id,
                actor_role=principal.role,
                source_name=source_name or "unnamed-input",
                content_type=content_type.split(";", 1)[0].strip().lower(),
                input_format=result.input_format,
                raw_bytes=raw,
                sha256=digest,
                idempotency_key=idempotency_key,
                validation_status=result.status,
                quarantine_reason=result.quarantine_reason,
                declared_source_orgs=list(result.declared_orgs),
                fixture_provenance=fixture_provenance,
            )
            session.add(envelope)
            session.flush()
            work = WorkIntent(
                org_id=principal.org_id,
                envelope_id=envelope.id,
                max_attempts=settings.worker_max_attempts,
            )
            session.add(work)
            _advance_tenant_revision(session, principal.org_id)
            session.add(
                AuditEvent(
                    org_id=principal.org_id,
                    actor_id=principal.actor_id,
                    event_type="RECEIVED",
                    subject_type="raw_envelope",
                    subject_id=str(envelope.id),
                    details={"sha256": digest, "validation_status": result.status},
                )
            )
            session.flush()
    except IntegrityError:
        replay = _replay_if_present(session, principal.org_id, idempotency_key, digest)
        if replay is not None:
            return replay
        raise
    return AcceptanceResult(envelope.id, work.id, False, result.status, result.quarantine_reason)
