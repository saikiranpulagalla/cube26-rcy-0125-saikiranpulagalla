from __future__ import annotations

import pytest

from recovery_manager.assessment import (
    SyntheticMechanicsAuthority,
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
    PolicySourceVersion,
    SourceRecordVersion,
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
        session.add(
            PolicySourceVersion(
                org_id=synthetic_org,
                policy_key="SYN-VALID-FEE-8",
                authority_class="SYNTHETIC",
                content_sha256="b" * 64,
                effective_from=None,
                effective_to=None,
                applicability={**provenance, "proposition_key": "synthetic-invalid-fee"},
                raw_text="SYNTHETIC MECHANICS — NOT ORGANIZER GROUND TRUTH OR REAL CHANNEL POLICY",
            )
        )
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
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance=provenance,
            quantity_scope={"coverage": "KNOWN", "quantity": "1"},
        )
        session.add(obligation)
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
                source_basis={**provenance, "synthetic_policy_key": "SYN-VALID-FEE-8"},
            )
        )
        session.flush()
        authority = SyntheticMechanicsAuthority(
            org_id=synthetic_org,
            fixture_profile="synthetic-mechanics-v1",
            fixture_sha256="a" * 64,
        )
        disabled = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_authority=authority,
        )
        assert disabled.conclusion == "REVIEW"
        with pytest.raises(ValueError, match="not current synthetic claim-ready"):
            reserve_synthetic_packet(session, synthetic_org, disabled.id, "disabled-export")
        mismatched_authority = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
            synthetic_authority=SyntheticMechanicsAuthority(
                org_id="org_demo_alpha",
                fixture_profile="synthetic-mechanics-v1",
                fixture_sha256="a" * 64,
            ),
        )
        assert mismatched_authority.conclusion == "REVIEW"
        wrong_subject = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "other-business-instance",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
            synthetic_authority=authority,
        )
        assert wrong_subject.conclusion == "REVIEW"
        assessment = assess_synthetic(
            session,
            synthetic_org,
            obligation.id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
            synthetic_authority=authority,
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
