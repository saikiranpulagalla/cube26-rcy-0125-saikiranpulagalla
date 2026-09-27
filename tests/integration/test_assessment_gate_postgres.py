from __future__ import annotations

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from recovery_manager.assessment import (
    assess_synthetic,
    current_assessment,
    reserve_synthetic_packet,
)
from recovery_manager.config import Principal
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    AmountDerivation,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceRecord,
    FinancialEvent,
    PolicySourceVersion,
    SourceRecordVersion,
    SyntheticFixtureProfile,
)


def _register_synthetic_profile(settings, org_id: str) -> None:
    owner_factory = sessionmaker(
        bind=create_engine(settings.migration_database_url, future=True), future=True
    )
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": "a" * 64}
    with owner_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        policy = PolicySourceVersion(
            org_id=org_id,
            policy_key="SYN-VALID-FEE-8",
            authority_class="SYNTHETIC",
            content_sha256="b" * 64,
            effective_from=None,
            effective_to=None,
            applicability={
                **provenance,
                "proposition_key": "synthetic-invalid-fee",
                "permitted_amount_minor": 800,
            },
            raw_text="SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY",
            lifecycle_state="ACTIVE",
        )
        session.add(policy)
        session.flush()
        session.add(
            SyntheticFixtureProfile(
                org_id=org_id,
                fixture_profile=provenance["fixture_profile"],
                fixture_sha256=provenance["fixture_sha256"],
                policy_source_version_id=policy.id,
            )
        )


def test_empty_decisive_evidence_cannot_produce_synthetic_ready(
    runtime_factory, alpha, settings
) -> None:
    """Regression: a residual plus a synthetic-looking derivation is not proof."""
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        accept_input(
            session,
            alpha,
            b"synthetic assessment fixture",
            "application/octet-stream",
            "synthetic-fixture",
            "synthetic-assessment-fixture",
            settings,
        )
        obligation = EconomicObligation(
            org_id="org_demo_alpha",
            economic_key="SYN-FEE-001",
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={
                "fixture_label": "SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY"
            },
            quantity_scope={"coverage": "KNOWN", "quantity": "1"},
        )
        session.add(obligation)
        session.flush()
        session.add(
            AmountDerivation(
                org_id="org_demo_alpha",
                obligation_id=obligation.id,
                derivation_version=1,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={"synthetic_policy_id": "SYN-VALID-FEE-8"},
            )
        )
        session.flush()
        assessment = assess_synthetic(
            session,
            "org_demo_alpha",
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
        )
        assert assessment.conclusion != "SYNTHETIC_CLAIM_READY"
        assert assessment.conclusion == "REVIEW"


def test_trusted_synthetic_control_is_ready_for_two_dollars(
    runtime_factory, alpha, settings
) -> None:
    """SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY."""
    synthetic_org = "org_synthetic_mechanics"
    synthetic_principal = Principal(
        org_id=synthetic_org, actor_id="synthetic_fixture", role="fixture_admin"
    )
    _register_synthetic_profile(settings, synthetic_org)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, synthetic_org)
        accept_input(
            session,
            synthetic_principal,
            b"synthetic fixture",
            "application/octet-stream",
            "synthetic",
            "control",
            settings,
        )
        provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": "a" * 64}
        source = SourceRecordVersion(
            org_id=synthetic_org,
            source_kind="synthetic",
            source_record_id="SYN-FEE-001",
            content_sha256="c" * 64,
            declared_org_id=synthetic_org,
            payload={"fee": {"valid": True}},
        )
        session.add(source)
        session.flush()
        event = FinancialEvent(
            org_id=synthetic_org,
            source_record_version_id=source.id,
            event_type="SYNTHETIC_FEE",
            direction="DEBIT",
            amount_minor=1000,
            currency="USD",
            quantity=1,
            posting_time=None,
            posting_time_precision=None,
            incident_time=None,
            incident_time_precision=None,
            business_references={"synthetic_fixture": "SYN-FEE-001"},
            normalized_fields={"synthetic": True},
        )
        session.add(event)
        session.flush()
        evidence = EvidenceRecord(
            org_id=synthetic_org,
            source_record_version_id=source.id,
            evidence_kind="SYNTHETIC",
            observed_time=None,
            observed_time_precision=None,
            coverage_quantity=1,
            coverage_scope={"coverage": "KNOWN"},
            normalized_fields={},
        )
        session.add(evidence)
        session.flush()
        obligation = EconomicObligation(
            org_id=synthetic_org,
            economic_key="SYN-FEE-001",
            financial_event_id=event.id,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance=provenance,
            quantity_scope={"coverage": "KNOWN", "quantity": "1"},
        )
        session.add(obligation)
        session.flush()
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(
                    EconomicObligation(
                        org_id=synthetic_org,
                        economic_key="SYN-FEE-001-DUPLICATE",
                        financial_event_id=event.id,
                        recovery_basis="INVALID_FEE",
                        currency="USD",
                        business_instance=provenance,
                        quantity_scope={"coverage": "KNOWN", "quantity": "1"},
                    )
                )
                session.flush()
        session.add(
            EvidenceAssertion(
                org_id=synthetic_org,
                evidence_record_id=evidence.id,
                source_record_version_id=source.id,
                proposition_key="synthetic-invalid-fee",
                subject_key="SYN-FEE-001",
                polarity="SUPPORTS",
                fact_path="fee.valid",
                asserted_value=True,
                scope={"coverage": "KNOWN"},
                decisive=True,
            )
        )
        session.add(
            AmountDerivation(
                org_id=synthetic_org,
                obligation_id=obligation.id,
                derivation_version=1,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis=provenance,
            )
        )
        session.flush()
        disabled = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
        )
        assert disabled.conclusion == "REVIEW"
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, synthetic_org, disabled.id, "disabled-export")
        wrong_subject = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "other-business-instance",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert wrong_subject.conclusion == "REVIEW"
        assessment = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert assessment.conclusion == "SYNTHETIC_CLAIM_READY"
        assert assessment.recoverable_minor == 200
        packet = reserve_synthetic_packet(session, synthetic_org, assessment.id, "synthetic-export")
        assert packet.packet["synthetic_only"] is True
        assert packet.packet["amount_minor"] == 200
        assert (
            reserve_synthetic_packet(session, synthetic_org, assessment.id, "synthetic-export").id
            == packet.id
        )
        view = current_assessment(session, synthetic_org, obligation.id)
        assert view is not None
        assert view.state == "STALE"

        session.add(
            AmountDerivation(
                org_id=synthetic_org,
                obligation_id=obligation.id,
                derivation_version=2,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=300,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis=provenance,
            )
        )
        session.flush()
        forged_entitlement = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert forged_entitlement.conclusion == "REVIEW"

        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    update(PolicySourceVersion)
                    .where(
                        PolicySourceVersion.org_id == synthetic_org,
                        PolicySourceVersion.policy_key == "SYN-VALID-FEE-8",
                    )
                    .values(lifecycle_state="REVOKED")
                )

        session.add(
            AmountDerivation(
                org_id=synthetic_org,
                obligation_id=obligation.id,
                derivation_version=3,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=800,
                justified_entitlement_minor=200,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={
                    "fixture_profile": "synthetic-mechanics-v1",
                    "fixture_sha256": "e" * 64,
                },
            )
        )
        session.flush()
        unregistered_fixture = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assert unregistered_fixture.conclusion == "REVIEW"


def test_runtime_cannot_register_synthetic_policy_authority(runtime_factory, settings) -> None:
    org_id = "org_untrusted_synthetic_policy"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        accept_input(
            session,
            Principal(org_id=org_id, actor_id="runtime", role="operator"),
            b"ordinary input",
            "application/octet-stream",
            "ordinary",
            "untrusted-policy",
            settings,
        )
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(
                    PolicySourceVersion(
                        org_id=org_id,
                        policy_key="SYN-VALID-FEE-8",
                        authority_class="SYNTHETIC",
                        content_sha256="d" * 64,
                        effective_from=None,
                        effective_to=None,
                        applicability={"fixture_profile": "synthetic-mechanics-v1"},
                        raw_text="forged synthetic authority",
                        lifecycle_state="ACTIVE",
                    )
                )
                session.flush()
