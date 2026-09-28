from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from recovery_manager.assessment import assess_synthetic
from recovery_manager.config import Principal
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    AmountDerivation,
    CurrentRecoveryRecommendation,
    EconomicObligation,
    RecoveryAssessment,
    TenantState,
)


def _accepted_tenant(session, settings, org_id: str, key: str) -> None:
    accept_input(
        session,
        Principal(org_id=org_id, actor_id="guarded-publication-test", role="operator"),
        key.encode(),
        "application/octet-stream",
        "guarded-publication",
        key,
        settings,
    )


def _publish_real_synthetic_fixture(
    runtime_factory, worker_factory, settings, org_id: str
) -> tuple[UUID, UUID]:
    from test_assessment_gate_postgres import _assess_synthetic_candidate

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
        return assessment.id, obligation_id


def test_runtime_cannot_directly_publish_or_fabricate_current_pointer(runtime_factory, settings) -> None:
    org_id = "org_guarded_publication"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _accepted_tenant(session, settings, org_id, "guarded-direct")
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="guarded-publication",
            recovery_basis="UNKNOWN",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        revision = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one()
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    text(
                        "INSERT INTO recovery_assessment "
                        "(id, org_id, obligation_id, tenant_revision, conclusion, recoverable_minor, currency, dependency_snapshot) "
                        "VALUES (:id, :org, :obligation, :revision, 'SYNTHETIC_CLAIM_READY', 200, 'USD', :snapshot)"
                    ),
                    {
                        "id": uuid4(),
                        "org": org_id,
                        "obligation": obligation.id,
                        "revision": revision,
                        "snapshot": '{"tenant_revision": 0}',
                    },
                )
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    text(
                        "INSERT INTO current_recovery_recommendation (org_id, obligation_id, assessment_id) "
                        "VALUES (:org, :obligation, :assessment)"
                    ),
                    {"org": org_id, "obligation": obligation.id, "assessment": uuid4()},
                )
        for conclusion in ("RESOLVED", "ALREADY_PURSUED"):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(
                        text(
                            "INSERT INTO recovery_assessment "
                            "(id, org_id, obligation_id, tenant_revision, conclusion, recoverable_minor, currency, dependency_snapshot) "
                            "VALUES (:id, :org, :obligation, :revision, :conclusion, NULL, NULL, :snapshot)"
                        ),
                        {
                            "id": uuid4(),
                            "org": org_id,
                            "obligation": obligation.id,
                            "revision": revision,
                            "conclusion": conclusion,
                            "snapshot": '{\"tenant_revision\": 0}',
                        },
                    )
        for statement in (
            text("UPDATE current_recovery_recommendation SET assessment_id = assessment_id WHERE false"),
            text("DELETE FROM current_recovery_recommendation WHERE false"),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(statement)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    text(
                        "SELECT public.publish_recovery_assessment("
                        ":assessment_id, :obligation_id, :revision, 'REVIEW', NULL, NULL, "
                        "CAST(:snapshot AS jsonb))"
                    ),
                    {
                        "assessment_id": uuid4(),
                        "obligation_id": obligation.id,
                        "revision": revision,
                        "snapshot": '{"tenant_revision": 0}',
                    },
                )


def test_worker_cannot_directly_publish_or_fabricate_current_pointer(worker_factory) -> None:
    org_id = "org_worker_publication_denied"
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        for statement in (
            text(
                "INSERT INTO recovery_assessment "
                "(id, org_id, obligation_id, tenant_revision, conclusion, recoverable_minor, currency, dependency_snapshot) "
                "VALUES (:id, :org, :obligation, 0, 'SYNTHETIC_CLAIM_READY', 200, 'USD', '{}'::jsonb)"
            ),
            text(
                "INSERT INTO current_recovery_recommendation (org_id, obligation_id, assessment_id) "
                "VALUES (:org, :obligation, :id)"
            ),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(
                        statement,
                        {"id": uuid4(), "org": org_id, "obligation": uuid4()},
                    )
        for conclusion in ("RESOLVED", "ALREADY_PURSUED"):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(
                        text(
                            "INSERT INTO recovery_assessment "
                            "(id, org_id, obligation_id, tenant_revision, conclusion, recoverable_minor, currency, dependency_snapshot) "
                            "VALUES (:id, :org, :obligation, 0, :conclusion, NULL, NULL, '{}'::jsonb)"
                        ),
                        {"id": uuid4(), "org": org_id, "obligation": uuid4(), "conclusion": conclusion},
                    )
        for statement in (
            text("UPDATE current_recovery_recommendation SET assessment_id = assessment_id WHERE false"),
            text("DELETE FROM current_recovery_recommendation WHERE false"),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(statement)


def test_assessment_service_publishes_immutable_current_and_historical_records(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_guarded_service"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _accepted_tenant(session, settings, org_id, "guarded-service")
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="guarded-service",
            recovery_basis="UNKNOWN",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        session.add(
            AmountDerivation(
                org_id=org_id,
                obligation_id=obligation.id,
                derivation_version=1,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={},
            )
        )
        session.flush()
        obligation_id = obligation.id
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        first = assess_synthetic(session, org_id, obligation_id, "missing", "missing")
        second = assess_synthetic(session, org_id, obligation_id, "missing", "missing")
        assert first.conclusion == second.conclusion == "REVIEW"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert (
            session.execute(
                select(CurrentRecoveryRecommendation.assessment_id).where(
                    CurrentRecoveryRecommendation.org_id == org_id,
                    CurrentRecoveryRecommendation.obligation_id == obligation_id,
                )
            ).scalar_one()
            == second.id
        )
        assert (
            session.execute(
                select(RecoveryAssessment).where(RecoveryAssessment.org_id == org_id)
            ).scalars().all()
        )


def test_guarded_publisher_rejects_invalid_references_and_stale_revision(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_guarded_invalid_publisher"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _accepted_tenant(session, settings, org_id, "guarded-invalid-publisher")
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="guarded-invalid-publisher",
            recovery_basis="UNKNOWN",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        obligation_id = obligation.id
        revision = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one()
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        for candidate_obligation, candidate_revision in (
            (uuid4(), revision),
            (obligation_id, revision - 1),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(
                        text(
                            "SELECT public.publish_recovery_assessment("
                            ":assessment_id, :obligation_id, :revision, 'REVIEW', NULL, NULL, "
                            "CAST(:snapshot AS jsonb))"
                        ),
                        {
                            "assessment_id": uuid4(),
                            "obligation_id": candidate_obligation,
                            "revision": candidate_revision,
                            "snapshot": json.dumps({"tenant_revision": candidate_revision}),
                        },
                    )


def test_current_pointer_database_guard_rejects_wrong_obligation_and_tenant(
    runtime_factory, worker_factory, settings
) -> None:
    alpha = "org_pointer_guard_alpha"
    bravo = "org_pointer_guard_bravo"
    alpha_assessment_id, alpha_obligation_id = _publish_real_synthetic_fixture(
        runtime_factory, worker_factory, settings, alpha
    )
    _, bravo_obligation_id = _publish_real_synthetic_fixture(
        runtime_factory, worker_factory, settings, bravo
    )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha)
        second_obligation = EconomicObligation(
            org_id=alpha,
            economic_key="pointer-guard-other-obligation",
            recovery_basis="UNKNOWN",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(second_obligation)
        session.flush()
        wrong_obligation_id = second_obligation.id
    owner_factory = sessionmaker(
        bind=create_engine(settings.migration_database_url, future=True), future=True
    )
    with owner_factory() as session, session.begin():
        set_local_tenant(session, alpha)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    text(
                        "INSERT INTO current_recovery_recommendation (org_id, obligation_id, assessment_id) "
                        "VALUES (:org, :obligation, :assessment)"
                    ),
                    {
                        "org": alpha,
                        "obligation": wrong_obligation_id,
                        "assessment": alpha_assessment_id,
                    },
                )
        set_local_tenant(session, bravo)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    text(
                        "INSERT INTO current_recovery_recommendation (org_id, obligation_id, assessment_id) "
                        "VALUES (:org, :obligation, :assessment)"
                    ),
                    {
                        "org": bravo,
                        "obligation": bravo_obligation_id,
                        "assessment": alpha_assessment_id,
                    },
                )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha)
        assert session.execute(
            select(CurrentRecoveryRecommendation.assessment_id).where(
                CurrentRecoveryRecommendation.org_id == alpha,
                CurrentRecoveryRecommendation.obligation_id == alpha_obligation_id,
            )
        ).scalar_one() == alpha_assessment_id


def test_guarded_publication_rolls_back_assessment_and_pointer_together(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_guarded_publication_rollback"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _accepted_tenant(session, settings, org_id, "guarded-publication-rollback")
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="guarded-publication-rollback",
            recovery_basis="UNKNOWN",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        obligation_id = obligation.id
        revision = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == org_id)
        ).scalar_one()
    with pytest.raises(RuntimeError, match="inject"):
        with worker_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            session.execute(
                text(
                    "SELECT public.publish_recovery_assessment("
                    ":assessment_id, :obligation_id, :revision, 'REVIEW', NULL, NULL, "
                    "CAST(:snapshot AS jsonb))"
                ),
                {
                    "assessment_id": uuid4(),
                    "obligation_id": obligation_id,
                    "revision": revision,
                    "snapshot": json.dumps({"tenant_revision": revision}),
                },
            )
            raise RuntimeError("inject before commit")
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(
            select(func.count()).select_from(RecoveryAssessment)
        ).scalar_one() == 0
        assert session.execute(
            select(func.count()).select_from(CurrentRecoveryRecommendation)
        ).scalar_one() == 0
