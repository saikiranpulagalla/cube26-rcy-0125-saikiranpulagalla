from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

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


def test_assessment_service_publishes_immutable_current_and_historical_records(
    runtime_factory, settings
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
        first = assess_synthetic(session, org_id, obligation.id, "missing", "missing")
        second = assess_synthetic(session, org_id, obligation.id, "missing", "missing")
        assert first.conclusion == second.conclusion == "REVIEW"
        assert (
            session.execute(
                select(CurrentRecoveryRecommendation.assessment_id).where(
                    CurrentRecoveryRecommendation.org_id == org_id,
                    CurrentRecoveryRecommendation.obligation_id == obligation.id,
                )
            ).scalar_one()
            == second.id
        )
        assert (
            session.execute(
                select(RecoveryAssessment).where(RecoveryAssessment.org_id == org_id)
            ).scalars().all()
        )
