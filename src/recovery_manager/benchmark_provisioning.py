"""Controlled synthetic world-state provisioning for demos and benchmarks.

This module creates inputs only. Assessment and publication remain the normal runtime path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Principal, Settings
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    AmountDerivation,
    EconomicObligation,
    EvidenceAssertion,
    EvidenceRecord,
    FinancialEvent,
    PolicySourceVersion,
    ReconciliationState,
    SourceRecordVersion,
    SyntheticFixtureProfile,
)


@dataclass(frozen=True)
class SyntheticRecoverySetup:
    evidence: str = "valid"
    reconciliation: str = "RECONCILED_NONE"
    policy_available: bool = True


@dataclass(frozen=True)
class ProvisionedRecoveryCase:
    org_id: str
    obligation_id: UUID
    logical_evidence: dict[str, UUID]


def provision_synthetic_recovery_case(
    session: Session, settings: Settings, org_id: str, setup: SyntheticRecoverySetup
) -> ProvisionedRecoveryCase:
    """Provision controlled inputs; never creates an assessment or current pointer."""
    set_local_tenant(session, org_id)
    payload = {"fee": {"valid": setup.evidence == "valid"}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    provenance = {"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": digest}
    accept_input(session, Principal(org_id, "benchmark_fixture", "fixture_admin"), b"benchmark fixture", "application/octet-stream", "benchmark", f"benchmark:{org_id}", settings)
    source = SourceRecordVersion(org_id=org_id, source_kind="synthetic", source_record_id="SYN-FEE-001", content_sha256=digest, declared_org_id=org_id, payload=payload)
    session.add(source)
    session.flush()
    event = FinancialEvent(org_id=org_id, source_record_version_id=source.id, event_type="SYNTHETIC_FEE", direction="DEBIT", amount_minor=1000, currency="USD", quantity=1, posting_time=None, posting_time_precision=None, incident_time=None, incident_time_precision=None, business_references={"synthetic_fixture": "SYN-FEE-001"}, normalized_fields={"synthetic": True})
    evidence = EvidenceRecord(org_id=org_id, source_record_version_id=source.id, evidence_kind="SYNTHETIC", observed_time=None, observed_time_precision=None, coverage_quantity=1 if setup.evidence == "valid" else 0, coverage_scope={"coverage": "KNOWN"}, normalized_fields={})
    session.add(event)
    session.flush()
    obligation = EconomicObligation(org_id=org_id, economic_key="SYN-FEE-001", financial_event_id=event.id, recovery_basis="INVALID_FEE", currency="USD", business_instance=provenance, quantity_scope={"coverage": "KNOWN", "quantity": "1"})
    session.add_all((evidence, obligation))
    session.flush()
    for domain in ("SETTLEMENT", "PURSUIT"):
        session.add(ReconciliationState(org_id=org_id, obligation_id=obligation.id, domain=domain, state=setup.reconciliation if domain == "SETTLEMENT" else "RECONCILED_NONE", cutoff=datetime.now(UTC), source_set_sha256=("e" if domain == "SETTLEMENT" else "f") * 64))
    session.add(EvidenceAssertion(org_id=org_id, evidence_record_id=evidence.id, source_record_version_id=source.id, proposition_key="synthetic-invalid-fee", subject_key="SYN-FEE-001", polarity="SUPPORTS", fact_path="fee.valid", asserted_value=setup.evidence == "valid", scope={"coverage": "KNOWN", "premise_key": "SYNTHETIC_VALID_FEE"}, decisive=True))
    session.add(AmountDerivation(org_id=org_id, obligation_id=obligation.id, derivation_version=1, currency="USD", observed_amount_minor=1000, expected_amount_minor=800, justified_entitlement_minor=200, rounding_rule="integer minor units", basis_class="SYNTHETIC_ONLY", source_basis=provenance))
    session.flush()
    return ProvisionedRecoveryCase(org_id, obligation.id, {"E-FEE-VALID": evidence.id})


def register_synthetic_authority(settings: Settings, org_id: str, setup: SyntheticRecoverySetup) -> None:
    """Perform owner-only fixture registration before the runtime transaction."""
    if not setup.policy_available:
        return
    payload = {"fee": {"valid": setup.evidence == "valid"}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    factory = sessionmaker(bind=create_engine(settings.migration_database_url, future=True), future=True)
    with factory() as session, session.begin():
        set_local_tenant(session, org_id)
        policy = PolicySourceVersion(org_id=org_id, policy_key="SYN-VALID-FEE-8", authority_class="SYNTHETIC", content_sha256="b" * 64, effective_from=None, effective_to=None, applicability={"fixture_profile": "synthetic-mechanics-v1", "fixture_sha256": digest, "proposition_key": "synthetic-invalid-fee", "permitted_amount_minor": 800}, raw_text="SYNTHETIC MECHANICS", lifecycle_state="ACTIVE")
        session.add(policy)
        session.flush()
        session.add(SyntheticFixtureProfile(org_id=org_id, fixture_profile="synthetic-mechanics-v1", fixture_sha256=digest, policy_source_version_id=policy.id))
