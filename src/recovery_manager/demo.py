"""Inspectable, application-owned synthetic Recovery demo orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.benchmark_provisioning import SyntheticRecoverySetup
from recovery_manager.config import Settings
from recovery_manager.engine_benchmark import EngineExecution, RecoveryEngineBenchmarkAdapter


@dataclass(frozen=True)
class DemoScenario:
    name: str
    setup: SyntheticRecoverySetup


DEMO_SCENARIOS = (
    DemoScenario("supported-synthetic", SyntheticRecoverySetup()),
    DemoScenario("evidence-insufficient", SyntheticRecoverySetup(evidence="insufficient")),
    DemoScenario("reconciliation-unknown", SyntheticRecoverySetup(reconciliation="UNKNOWN")),
    DemoScenario("policy-unavailable", SyntheticRecoverySetup(policy_available=False)),
    DemoScenario("partial-settlement", SyntheticRecoverySetup(settlement_minor=100)),
    DemoScenario("fully-settled", SyntheticRecoverySetup(settlement_minor=200)),
    DemoScenario("partial-active-pursuit", SyntheticRecoverySetup(pursuit_minor=100)),
    DemoScenario("fully-pursued", SyntheticRecoverySetup(pursuit_minor=200)),
)


def run_demo(
    runtime: sessionmaker[Session], worker: sessionmaker[Session], settings: Settings
) -> list[tuple[DemoScenario, EngineExecution]]:
    """Execute only real persisted assessments; no expected output enters this path."""
    adapter = RecoveryEngineBenchmarkAdapter(runtime, worker, settings)
    return [(scenario, adapter.run(scenario.setup)) for scenario in DEMO_SCENARIOS]
