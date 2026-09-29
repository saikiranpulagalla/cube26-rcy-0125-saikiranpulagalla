from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import text

from recovery_manager.benchmark_provisioning import (
    BENCHMARK_LOCK_TIMEOUT,
    BENCHMARK_STATEMENT_TIMEOUT,
    SyntheticRecoverySetup,
    configure_benchmark_transaction,
)
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
    assert prediction.predicted_opportunity_id == setup.logical_opportunity_id
    assert prediction.predicted_obligation_id == setup.logical_obligation_id


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


@pytest.mark.parametrize(
    ("setup", "recommendation", "amount"),
    (
        (SyntheticRecoverySetup(settlement_minor=0), "SYNTHETIC_CLAIM_READY", 200),
        (SyntheticRecoverySetup(settlement_minor=100), "SYNTHETIC_CLAIM_READY", 100),
        (SyntheticRecoverySetup(settlement_minor=200), "RESOLVED", None),
        (SyntheticRecoverySetup(pursuit_minor=100), "SYNTHETIC_CLAIM_READY", 100),
        (SyntheticRecoverySetup(pursuit_minor=200), "ALREADY_PURSUED", None),
    ),
)
def test_adapter_projects_declared_ledger_setup(
    runtime_factory, worker_factory, settings, setup, recommendation, amount
) -> None:
    prediction = RecoveryEngineBenchmarkAdapter(runtime_factory, worker_factory, settings).run(
        setup
    ).prediction
    assert prediction.predicted_recommendation == recommendation
    assert prediction.predicted_amount_minor == amount


def test_adapter_reverse_maps_persisted_identities_without_truth(
    runtime_factory, worker_factory, settings
) -> None:
    prediction = RecoveryEngineBenchmarkAdapter(runtime_factory, worker_factory, settings).run(
        SyntheticRecoverySetup(
            logical_opportunity_id="OPP-B", logical_obligation_id="OBL-B", logical_evidence_id="E2"
        )
    ).prediction
    assert prediction.predicted_opportunity_id == "OPP-B"
    assert prediction.predicted_obligation_id == "OBL-B"
    assert prediction.predicted_evidence == {"E2"}
    mismatched_truth = EvaluationCase(
        scenario_id="holdout/mismatch", dependency_group="mismatch", stratum="SYNTHETIC_MECHANICS",
        expected_recommendation="SYNTHETIC_CLAIM_READY", expected_amount_minor=200,
        predicted_recommendation=prediction.predicted_recommendation,
        predicted_amount_minor=prediction.predicted_amount_minor,
        expected_opportunity_id="OPP-A", predicted_opportunity_id=prediction.predicted_opportunity_id,
        expected_obligation_id="OBL-A", predicted_obligation_id=prediction.predicted_obligation_id,
        expected_basis="INVALID_FEE", predicted_basis=prediction.predicted_basis,
        expected_currency="USD", predicted_currency=prediction.predicted_currency,
        required_evidence=frozenset({"E1"}), predicted_evidence=prediction.predicted_evidence,
    )
    assert metrics([mismatched_truth])["strict_true_positives"] == 0


def test_adapter_fails_closed_for_unmapped_pinned_evidence(
    runtime_factory, worker_factory, settings, monkeypatch
) -> None:
    import recovery_manager.engine_benchmark as module

    original = module.provision_synthetic_recovery_case

    def provision_with_missing_evidence(*args, **kwargs):
        provisioned = original(*args, **kwargs)
        return replace(provisioned, logical_evidence={})

    monkeypatch.setattr(module, "provision_synthetic_recovery_case", provision_with_missing_evidence)
    with pytest.raises(RuntimeError, match="unmapped evidence"):
        RecoveryEngineBenchmarkAdapter(runtime_factory, worker_factory, settings).run(
            SyntheticRecoverySetup()
        )


def test_benchmark_transactions_have_bounded_database_timeouts(runtime_factory) -> None:
    with runtime_factory() as session, session.begin():
        configure_benchmark_transaction(session)
        lock_timeout, statement_timeout = session.execute(
            text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
        ).one()
    assert lock_timeout == BENCHMARK_LOCK_TIMEOUT
    assert statement_timeout == BENCHMARK_STATEMENT_TIMEOUT
