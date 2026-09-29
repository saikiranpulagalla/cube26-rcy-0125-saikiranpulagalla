"""Real PostgreSQL benchmark adapter; it receives setup, never ground truth."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.assessment import assess_synthetic, current_assessment
from recovery_manager.benchmark_provisioning import (
    SyntheticRecoverySetup,
    provision_synthetic_recovery_case,
    register_synthetic_authority,
)
from recovery_manager.config import Settings
from recovery_manager.db import set_local_tenant
from recovery_manager.evaluation import EvaluationCase
from recovery_manager.models import RecoveryAssessment


@dataclass(frozen=True)
class EngineExecution:
    prediction: EvaluationCase
    assessment_id: str

class RecoveryEngineBenchmarkAdapter:
    def __init__(self, runtime: sessionmaker[Session], worker: sessionmaker[Session], settings: Settings):
        self.runtime, self.worker, self.settings = runtime, worker, settings

    def run(self, setup: SyntheticRecoverySetup) -> EngineExecution:
        org_id = f"benchmark_{uuid4().hex}"
        register_synthetic_authority(self.settings, org_id, setup)
        with self.runtime() as session, session.begin():
            provisioned = provision_synthetic_recovery_case(session, self.settings, org_id, setup)
        with self.worker() as session, session.begin():
            set_local_tenant(session, org_id)
            assessment = assess_synthetic(session, org_id, provisioned.obligation_id, "SYN-FEE-001", "synthetic-invalid-fee", synthetic_capability_enabled=True)
        with self.runtime() as session, session.begin():
            set_local_tenant(session, org_id)
            stored = session.execute(select(RecoveryAssessment).where(RecoveryAssessment.org_id == org_id, RecoveryAssessment.id == assessment.id)).scalar_one()
            current = current_assessment(session, org_id, provisioned.obligation_id)
            if current is None or current.assessment.id != stored.id:
                raise RuntimeError("guarded publication did not install current assessment")
        snapshot = stored.dependency_snapshot
        obligation_snapshot = snapshot.get("obligation")
        if not isinstance(obligation_snapshot, dict):
            raise RuntimeError("published assessment is missing its obligation snapshot")
        recovery_basis = obligation_snapshot.get("recovery_basis")
        if recovery_basis is not None and not isinstance(recovery_basis, str):
            raise RuntimeError("published assessment has an invalid recovery basis")
        snapshot_evidence = snapshot.get("evidence", [])
        if not isinstance(snapshot_evidence, list) or not all(
            isinstance(entry, dict) and isinstance(entry.get("evidence_record_id"), str)
            for entry in snapshot_evidence
        ):
            raise RuntimeError("published assessment has an invalid evidence snapshot")
        evidence_ids = {entry["evidence_record_id"] for entry in snapshot_evidence}
        evidence = frozenset(
            key for key, value in provisioned.logical_evidence.items() if str(value) in evidence_ids
        )
        return EngineExecution(EvaluationCase("engine/result", "engine", "SYNTHETIC_MECHANICS", "REVIEW", None, stored.conclusion, stored.recoverable_minor, expected_opportunity_id="", predicted_opportunity_id="", expected_obligation_id=None, predicted_obligation_id=str(provisioned.obligation_id), expected_basis="", predicted_basis=recovery_basis, expected_currency=None, predicted_currency=stored.currency, predicted_evidence=evidence), str(stored.id))
