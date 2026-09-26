"""v0.4 deterministic evidence proof and retrieval primitives; no financial decision logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from recovery_manager.models import EvidenceAssertion, EvidenceLifecycleEvent, SourceRecordVersion


@dataclass(frozen=True)
class EvidenceProof:
    assertion_id: UUID
    mechanically_supported: bool
    current: bool
    reason: str | None


@dataclass(frozen=True)
class RetrievalResult:
    assertions: tuple[EvidenceAssertion, ...]
    complete: bool
    conflict_present: bool


def _field(payload: dict[str, Any], path: str) -> object:
    if not path or path.startswith(".") or ".." in path:
        raise ValueError("fact path must be a nonempty dotted object path")
    value: object = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def prove_assertion(session: Session, org_id: str, assertion: EvidenceAssertion) -> EvidenceProof:
    source = session.execute(
        select(SourceRecordVersion).where(
            SourceRecordVersion.org_id == org_id,
            SourceRecordVersion.id == assertion.source_record_version_id,
        )
    ).scalar_one_or_none()
    if source is None:
        return EvidenceProof(assertion.id, False, False, "SOURCE_VERSION_NOT_VISIBLE")
    lifecycle = session.execute(
        select(EvidenceLifecycleEvent.state)
        .where(
            EvidenceLifecycleEvent.org_id == org_id,
            EvidenceLifecycleEvent.assertion_id == assertion.id,
        )
        .order_by(EvidenceLifecycleEvent.created_at.desc(), EvidenceLifecycleEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if lifecycle in {"REVOKED", "SUPERSEDED"}:
        return EvidenceProof(assertion.id, False, False, f"EVIDENCE_{lifecycle}")
    try:
        actual = _field(source.payload, assertion.fact_path)
    except (KeyError, ValueError):
        return EvidenceProof(
            assertion.id, False, lifecycle is None or lifecycle == "AVAILABLE", "FACT_PATH_MISSING"
        )
    if actual != assertion.asserted_value:
        return EvidenceProof(
            assertion.id,
            False,
            lifecycle is None or lifecycle == "AVAILABLE",
            "FACT_VALUE_MISMATCH",
        )
    return EvidenceProof(assertion.id, True, lifecycle is None or lifecycle == "AVAILABLE", None)


def discover_assertions(
    session: Session, org_id: str, subject_key: str, proposition_key: str, *, limit: int
) -> RetrievalResult:
    if limit < 1:
        raise ValueError("retrieval limit must be positive")
    rows = (
        session.execute(
            select(EvidenceAssertion)
            .where(
                EvidenceAssertion.org_id == org_id,
                EvidenceAssertion.subject_key == subject_key,
                EvidenceAssertion.proposition_key == proposition_key,
            )
            .order_by(EvidenceAssertion.created_at, EvidenceAssertion.id)
            .limit(limit + 1)
        )
        .scalars()
        .all()
    )
    complete = len(rows) <= limit
    selected = tuple(rows[:limit])
    polarities = {row.polarity for row in selected}
    return RetrievalResult(selected, complete, polarities == {"SUPPORTS", "CONTRADICTS"})
