from __future__ import annotations

from uuid import UUID

from recovery_manager.demo import run_demo


def test_application_owned_demo_executes_real_persisted_scenarios(
    runtime_factory, worker_factory, settings
) -> None:
    executions = run_demo(runtime_factory, worker_factory, settings)
    observed = {
        scenario.name: (
            execution.prediction.predicted_recommendation,
            execution.prediction.predicted_amount_minor,
        )
        for scenario, execution in executions
    }
    assert observed == {
        "supported-synthetic": ("SYNTHETIC_CLAIM_READY", 200),
        "evidence-insufficient": ("REVIEW", None),
        "reconciliation-unknown": ("REVIEW", None),
        "policy-unavailable": ("REVIEW", None),
        "partial-settlement": ("SYNTHETIC_CLAIM_READY", 100),
        "fully-settled": ("RESOLVED", None),
        "partial-active-pursuit": ("SYNTHETIC_CLAIM_READY", 100),
        "fully-pursued": ("ALREADY_PURSUED", None),
    }
    for _, execution in executions:
        assert UUID(execution.assessment_id)
        assert UUID(execution.persisted_opportunity_id)
        assert UUID(execution.persisted_obligation_id)

