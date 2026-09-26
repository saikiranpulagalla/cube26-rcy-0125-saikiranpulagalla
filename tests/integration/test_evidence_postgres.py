from __future__ import annotations

from sqlalchemy import select

from recovery_manager.db import set_local_tenant
from recovery_manager.evidence import discover_assertions, prove_assertion
from recovery_manager.models import (
    EvidenceAssertion,
    EvidenceLifecycleEvent,
    EvidenceRecord,
    SourceRecordVersion,
)


def _assertion(session, key: str, polarity: str = "SUPPORTS") -> EvidenceAssertion:  # type: ignore[no-untyped-def]
    source = SourceRecordVersion(
        org_id="org_demo_alpha",
        source_kind="test_evidence",
        source_record_id=key,
        content_sha256=(key.encode().hex() * 64)[:64],
        declared_org_id="org_demo_alpha",
        payload={"check": {"value": key}},
    )
    session.add(source)
    session.flush()
    evidence = EvidenceRecord(
        org_id="org_demo_alpha",
        source_record_version_id=source.id,
        evidence_kind="TEST",
        observed_time=None,
        observed_time_precision=None,
        coverage_quantity=None,
        coverage_scope={"coverage": "UNKNOWN"},
        normalized_fields={},
    )
    session.add(evidence)
    session.flush()
    assertion = EvidenceAssertion(
        org_id="org_demo_alpha",
        evidence_record_id=evidence.id,
        source_record_version_id=source.id,
        proposition_key="synthetic-proposition",
        subject_key="synthetic-subject",
        polarity=polarity,
        fact_path="check.value",
        asserted_value=key,
        scope={"coverage": "UNKNOWN"},
        decisive=True,
    )
    session.add(assertion)
    session.flush()
    return assertion


def test_evidence_assertion_requires_actual_source_fact_and_lifecycle_currentness(
    runtime_factory,
) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        assertion = _assertion(session, "true")
        assert prove_assertion(session, "org_demo_alpha", assertion).mechanically_supported
        session.add(
            EvidenceLifecycleEvent(
                org_id="org_demo_alpha", assertion_id=assertion.id, state="REVOKED", reason="test"
            )
        )
        session.flush()
        proof = prove_assertion(session, "org_demo_alpha", assertion)
        assert not proof.mechanically_supported
        assert proof.reason == "EVIDENCE_REVOKED"


def test_retrieval_is_deterministic_reports_conflict_and_reports_limit_incompleteness(
    runtime_factory,
) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        _assertion(session, "one", "SUPPORTS")
        _assertion(session, "two", "CONTRADICTS")
        _assertion(session, "three", "SUPPORTS")
        limited = discover_assertions(
            session, "org_demo_alpha", "synthetic-subject", "synthetic-proposition", limit=2
        )
        assert not limited.complete
        assert limited.conflict_present
        full = discover_assertions(
            session, "org_demo_alpha", "synthetic-subject", "synthetic-proposition", limit=3
        )
        assert full.complete and full.conflict_present
        assert [item.id for item in full.assertions] == [
            item.id
            for item in session.execute(
                select(EvidenceAssertion).order_by(
                    EvidenceAssertion.created_at, EvidenceAssertion.id
                )
            )
            .scalars()
            .all()
        ]
