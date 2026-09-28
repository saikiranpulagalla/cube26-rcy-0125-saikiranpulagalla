from pathlib import Path

import pytest

from recovery_manager.evaluation import (
    EvaluationCase,
    metrics,
    run_manifest,
    validate_split,
    write_report,
)


def _case(**changes: object) -> EvaluationCase:
    values: dict[str, object] = {
        "scenario_id": "holdout/a", "dependency_group": "family", "dependency_ids": ("event-1",),
        "stratum": "SYNTHETIC_MECHANICS", "expected_recommendation": "SYNTHETIC_CLAIM_READY",
        "expected_amount_minor": 200, "predicted_recommendation": "SYNTHETIC_CLAIM_READY",
        "predicted_amount_minor": 200, "expected_opportunity_id": "opp-1", "predicted_opportunity_id": "opp-1",
        "expected_obligation_id": "obl-1", "predicted_obligation_id": "obl-1", "expected_basis": "INVALID_FEE",
        "predicted_basis": "INVALID_FEE", "expected_currency": "USD", "predicted_currency": "USD",
        "required_evidence": frozenset({"premise|source-v1|subject|fact-1"}),
        "predicted_evidence": frozenset({"premise|source-v1|subject|fact-1"}),
    }
    values.update(changes)
    return EvaluationCase(**values)  # type: ignore[arg-type]


def test_strict_matching_requires_evidence_basis_and_opportunity() -> None:
    result = metrics([
        _case(predicted_evidence=frozenset({"premise|wrong-v|subject|fact-9"})),
        _case(scenario_id="holdout/b", dependency_group="family-b", predicted_basis="OTHER"),
        _case(scenario_id="holdout/c", dependency_group="family-c", predicted_opportunity_id="other"),
    ])
    assert result["strict_true_positives"] == 0
    assert result["strict_claim_precision"] == 0.0


def test_metrics_keep_zero_prediction_precision_na_and_strata_separate() -> None:
    review = _case(
        stratum="POLICY_UNAVAILABLE", expected_recommendation="REVIEW", expected_amount_minor=None,
        predicted_recommendation="REVIEW", predicted_amount_minor=None,
    )
    result = metrics([review])
    assert result["strict_claim_precision"] is None
    assert result["decision_coverage"] == 0.0
    assert set(result["strata"]) == {"POLICY_UNAVAILABLE"}


def test_duplicate_predictions_currency_exposure_and_empty_data_are_safe() -> None:
    duplicate = _case(scenario_id="holdout/duplicate", dependency_group="family-two")
    eur_wrong = _case(
        scenario_id="holdout/eur", dependency_group="family-eur", predicted_currency="EUR",
        predicted_evidence=frozenset({"wrong"}),
    )
    result = metrics([_case(), duplicate, eur_wrong])
    assert result["strict_true_positives"] == 1
    assert result["strict_claim_precision"] == 1 / 3
    assert result["false_exposure_minor"] == {"USD": 200, "EUR": 200}
    assert metrics([])["decision_coverage"] is None


def test_dependency_groups_cannot_leak_between_splits() -> None:
    cases = [
        EvaluationCase("dev/a", "shared", "SYNTHETIC_MECHANICS", "REVIEW", None, "REVIEW", None, True, True),
        EvaluationCase("validation/b", "shared", "SYNTHETIC_MECHANICS", "REVIEW", None, "REVIEW", None, True, True),
    ]
    with pytest.raises(ValueError, match="dependency groups"):
        validate_split(cases)


def test_actual_dependency_edges_cannot_be_split_by_forged_group() -> None:
    left = _case(scenario_id="dev/a", dependency_group="caller-a", dependency_ids=("shared-source",))
    right = _case(scenario_id="holdout/b", dependency_group="caller-b", dependency_ids=("shared-source",))
    with pytest.raises(ValueError, match="dependency groups"):
        validate_split([left, right])


def test_manifest_runner_emits_reproducible_reports(tmp_path: Path) -> None:
    manifest = Path("data/evaluation/repair09-benchmark.json")
    report = run_manifest(manifest, lambda item: item)
    json_path, markdown_path = tmp_path / "result.json", tmp_path / "result.md"
    write_report(report, json_path, markdown_path)
    assert report["results"]["cases"] == 2
    assert json_path.exists()
    assert "Operational recovery-policy accuracy" in markdown_path.read_text(encoding="utf-8")
