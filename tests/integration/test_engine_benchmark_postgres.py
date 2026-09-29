from __future__ import annotations

import pytest

from recovery_manager.benchmark_provisioning import SyntheticRecoverySetup
from recovery_manager.engine_benchmark import RecoveryEngineBenchmarkAdapter
from recovery_manager.evaluation import EvaluationCase, metrics


@pytest.mark.parametrize(
    ("setup", "recommendation", "amount"),
    (
        (SyntheticRecoverySetup(), "SYNTHETIC_CLAIM_READY", 200),
        (SyntheticRecoverySetup(policy_available=False), "REVIEW", None),
        (SyntheticRecoverySetup(evidence="insufficient"), "REVIEW", None),
        (SyntheticRecoverySetup(reconciliation="UNKNOWN"), "REVIEW", None),
    ),
)
def test_real_engine_adapter_executes_prediction_free_world_state(
    runtime_factory, worker_factory, settings, setup, recommendation, amount
) -> None:
    execution = RecoveryEngineBenchmarkAdapter(runtime_factory, worker_factory, settings).run(setup)
    prediction = execution.prediction
    assert prediction.predicted_recommendation == recommendation
    assert prediction.predicted_amount_minor == amount
    if recommendation == "SYNTHETIC_CLAIM_READY":
        assert prediction.predicted_currency == "USD"
        assert prediction.predicted_basis == "INVALID_FEE"
        assert prediction.predicted_evidence == {"E-FEE-VALID"}


def test_adapter_prediction_is_independent_of_evaluator_truth(runtime_factory, worker_factory, settings) -> None:
    adapter = RecoveryEngineBenchmarkAdapter(runtime_factory, worker_factory, settings)
    first = adapter.run(SyntheticRecoverySetup()).prediction
    second = adapter.run(SyntheticRecoverySetup()).prediction
    assert (
        first.predicted_recommendation,
        first.predicted_amount_minor,
        first.predicted_currency,
        first.predicted_evidence,
    ) == (
        second.predicted_recommendation,
        second.predicted_amount_minor,
        second.predicted_currency,
        second.predicted_evidence,
    )


def test_adapter_basis_comes_from_persisted_snapshot_not_evaluator_truth(
    runtime_factory, worker_factory, settings
) -> None:
    prediction = RecoveryEngineBenchmarkAdapter(runtime_factory, worker_factory, settings).run(
        SyntheticRecoverySetup()
    ).prediction
    assert prediction.predicted_basis == "INVALID_FEE"
    altered_truth = EvaluationCase(
        scenario_id="holdout/basis", dependency_group="basis", stratum="SYNTHETIC_MECHANICS",
        expected_recommendation="SYNTHETIC_CLAIM_READY", expected_amount_minor=200,
        predicted_recommendation=prediction.predicted_recommendation,
        predicted_amount_minor=prediction.predicted_amount_minor,
        expected_opportunity_id="opportunity", predicted_opportunity_id="opportunity",
        expected_obligation_id="obligation", predicted_obligation_id="obligation",
        expected_basis="ELIGIBLE_LOSS_DAMAGE", predicted_basis=prediction.predicted_basis,
        expected_currency="USD", predicted_currency=prediction.predicted_currency,
        required_evidence=prediction.predicted_evidence, predicted_evidence=prediction.predicted_evidence,
    )
    assert metrics([altered_truth])["strict_true_positives"] == 0
