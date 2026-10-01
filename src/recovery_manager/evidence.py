"""v0.4 deterministic evidence proof and retrieval primitives; no financial decision logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from recovery_manager.models import (
    EvidenceAssertion,
    EvidenceLifecycleEvent,
    EvidenceRecord,
    SourceRecordVersion,
)


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


def _json_values_equal(actual: object, asserted: object) -> bool:
    """Compare JSON values without Python's boolean-as-integer equivalence.

    JSON has one number type, so integral and decimal numeric values compare
    numerically.  Booleans remain a distinct JSON type at every nesting level.
    """
    if isinstance(actual, bool) or isinstance(asserted, bool):
        return isinstance(actual, bool) and isinstance(asserted, bool) and actual is asserted
    if isinstance(actual, int | float) or isinstance(asserted, int | float):
        return (
            isinstance(actual, int | float)
            and not isinstance(actual, bool)
            and isinstance(asserted, int | float)
            and not isinstance(asserted, bool)
            and actual == asserted
        )
    if actual is None or asserted is None:
        return actual is None and asserted is None
    if isinstance(actual, str) or isinstance(asserted, str):
        return isinstance(actual, str) and isinstance(asserted, str) and actual == asserted
    if isinstance(actual, list) or isinstance(asserted, list):
        return (
            isinstance(actual, list)
            and isinstance(asserted, list)
            and len(actual) == len(asserted)
            and all(_json_values_equal(left, right) for left, right in zip(actual, asserted, strict=True))
        )
    if isinstance(actual, dict) or isinstance(asserted, dict):
        return (
            isinstance(actual, dict)
            and isinstance(asserted, dict)
            and actual.keys() == asserted.keys()
            and all(_json_values_equal(actual[key], asserted[key]) for key in actual)
        )
    return False


def prove_assertion(session: Session, org_id: str, assertion: EvidenceAssertion) -> EvidenceProof:
    evidence = session.execute(
        select(EvidenceRecord).where(
            EvidenceRecord.org_id == org_id,
            EvidenceRecord.id == assertion.evidence_record_id,
        )
    ).scalar_one_or_none()
    if evidence is None or evidence.source_record_version_id != assertion.source_record_version_id:
        return EvidenceProof(assertion.id, False, False, "EVIDENCE_SOURCE_VERSION_MISMATCH")
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
    if not _json_values_equal(actual, assertion.asserted_value):
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
    # Conflict discovery is intentionally independent of the bounded result set.
    # A context/candidate limit may make retrieval incomplete, but it must never
    # turn an omitted adverse assertion into a claim that no conflict exists.
    polarities = set(
        session.execute(
            select(EvidenceAssertion.polarity)
            .where(
                EvidenceAssertion.org_id == org_id,
                EvidenceAssertion.subject_key == subject_key,
                EvidenceAssertion.proposition_key == proposition_key,
            )
            .distinct()
        )
        .scalars()
        .all()
    )
    return RetrievalResult(selected, complete, polarities == {"SUPPORTS", "CONTRADICTS"})
