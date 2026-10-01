from dataclasses import replace
from pathlib import Path

import pytest

from recovery_manager.evaluation import (
    EvaluationCase,
    load_engine_benchmark,
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


def test_identical_duplicate_claim_occurrence_counts_as_unsupported_exposure() -> None:
    """A second equal-valued prediction remains an unmatched occurrence."""
    supported = _case()
    duplicate = _case()
    result = metrics([supported, duplicate])
    assert result["strict_true_positives"] == 1
    assert result["claims_recommended"] == 2
    assert result["strict_claim_precision"] == 0.5
    assert result["false_exposure_minor"] == {"USD": 200}


def test_one_to_one_matching_tracks_prediction_occurrences_and_currency_buckets() -> None:
    second = _case(
        scenario_id="holdout/second", dependency_group="family-second",
        expected_opportunity_id="opp-2", predicted_opportunity_id="opp-2",
        expected_obligation_id="obl-2", predicted_obligation_id="obl-2",
    )
    duplicate_usd = _case()
    duplicate_eur = _case(
        scenario_id="holdout/eur-duplicate", dependency_group="family-eur-duplicate",
        predicted_currency="EUR", predicted_evidence=frozenset({"wrong"}),
    )
    result = metrics([_case(), second, duplicate_usd, duplicate_eur])
    assert result["strict_true_positives"] == 2
    assert result["claims_recommended"] == 4
    assert result["strict_claim_precision"] == 0.5
    assert result["false_exposure_minor"] == {"USD": 200, "EUR": 200}


def test_two_truths_and_three_matching_predictions_leave_one_unmatched() -> None:
    second = _case(
        scenario_id="holdout/second-only", dependency_group="family-second-only",
        expected_opportunity_id="opp-2", predicted_opportunity_id="opp-2",
        expected_obligation_id="obl-2", predicted_obligation_id="obl-2",
    )
    result = metrics([_case(), second, _case()])
    assert result["strict_true_positives"] == 2
    assert result["claims_recommended"] == 3
    assert result["false_exposure_minor"] == {"USD": 200}


def test_conflicting_obligation_truth_rejects_every_input_order() -> None:
    claim = _case(
        scenario_id="holdout/claim", expected_opportunity_id="O1", predicted_opportunity_id="O1",
        expected_obligation_id="OB-A", predicted_obligation_id="OB-A",
    )
    review_prediction = _case(
        scenario_id="holdout/review", expected_opportunity_id="O1", predicted_opportunity_id="O1",
        expected_obligation_id="OB-B", predicted_obligation_id="OB-B",
        expected_recommendation="REVIEW", expected_amount_minor=None, expected_currency=None,
        predicted_recommendation="REVIEW", predicted_amount_minor=None,
    )
    for ordered in ((claim, review_prediction), (review_prediction, claim)):
        with pytest.raises(ValueError, match="conflicting ground truth"):
            metrics(list(ordered))


@pytest.mark.parametrize(
    "changes",
    (
        {"expected_obligation_id": "OB-OTHER"},
        {"expected_recommendation": "REVIEW"},
        {"expected_basis": "OTHER"},
        {"expected_amount_minor": 201},
        {"expected_currency": "EUR"},
        {"required_evidence": frozenset({"different-evidence"})},
        {"stratum": "POLICY_UNAVAILABLE"},
    ),
)
def test_every_decisive_truth_conflict_is_rejected(changes: dict[str, object]) -> None:
    original = _case(expected_opportunity_id="O1", predicted_opportunity_id="O1")
    conflicting = replace(original, scenario_id="holdout/conflict", **changes)
    with pytest.raises(ValueError, match="conflicting ground truth"):
        metrics([original, conflicting])


def test_identical_duplicate_truth_is_idempotent() -> None:
    truth_and_prediction = _case(expected_opportunity_id="O1", predicted_opportunity_id="O1")
    duplicate_truth = replace(
        truth_and_prediction,
        scenario_id="holdout/duplicate-truth",
        predicted_recommendation="REVIEW",
        predicted_amount_minor=None,
    )
    result = metrics([truth_and_prediction, duplicate_truth])
    assert result["strict_true_positives"] == 1
    assert result["claims_recommended"] == 1
    assert result == metrics([duplicate_truth, truth_and_prediction])


def test_prediction_permutation_is_order_independent() -> None:
    supported = _case(
        scenario_id="holdout/supported", expected_opportunity_id="O1", predicted_opportunity_id="O1",
    )
    duplicate_prediction = replace(supported, scenario_id="holdout/duplicate")
    assert metrics([supported, duplicate_prediction]) == metrics([duplicate_prediction, supported])


@pytest.mark.parametrize("amount", (0, -1, True, False, 1.0, 200.0, "200", "0", None, [], {}))
def test_invalid_predicted_claim_amount_is_rejected(amount: object) -> None:
    with pytest.raises(ValueError, match="predicted claim amount"):
        metrics([_case(predicted_amount_minor=amount)])  # type: ignore[arg-type]


@pytest.mark.parametrize("amount", (0, -1, True, 1.0, "200", None))
def test_invalid_truth_claim_amount_is_rejected(amount: object) -> None:
    with pytest.raises(ValueError, match="expected claim amount"):
        metrics([_case(expected_amount_minor=amount)])  # type: ignore[arg-type]


@pytest.mark.parametrize("amount", (1, 200, 2**31))
def test_positive_integer_claim_amounts_remain_valid(amount: int) -> None:
    result = metrics([_case(expected_amount_minor=amount, predicted_amount_minor=amount)])
    assert result["strict_true_positives"] == 1


@pytest.mark.parametrize("currency", (None, ""))
def test_claim_currency_is_required_for_truth_and_prediction(currency: object) -> None:
    with pytest.raises(ValueError, match="claim currency"):
        metrics([_case(predicted_currency=currency)])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim currency"):
        metrics([_case(expected_currency=currency)])  # type: ignore[arg-type]


def test_non_claim_recommendations_allow_null_amount() -> None:
    review = _case(
        expected_recommendation="REVIEW", expected_amount_minor=None, expected_currency=None,
        predicted_recommendation="REVIEW", predicted_amount_minor=None, predicted_currency=None,
    )
    assert metrics([review])["claims_recommended"] == 0


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


def test_engine_benchmark_rejects_recorded_prediction_oracles(tmp_path: Path) -> None:
    manifest = tmp_path / "engine.json"
    manifest.write_text(
        '{"schema_version":"recovery-engine-benchmark/v1","cases":[{"case_id":"x","stratum":"SYNTHETIC_MECHANICS","setup":{},"ground_truth":{},"predicted_recommendation":"REVIEW"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="recorded predictions"):
        load_engine_benchmark(manifest)


@pytest.mark.parametrize(
    "forbidden_setup",
    (
        '{"prediction":{"recommendation":"REVIEW"}}',
        '{"nested":{"predicted_amount":200}}',
        '{"engine_output":{"recommendation":"REVIEW"}}',
    ),
)
def test_engine_benchmark_rejects_nested_prediction_oracles(
    tmp_path: Path, forbidden_setup: str
) -> None:
    manifest = tmp_path / "engine.json"
    manifest.write_text(
        "{"
        '\"schema_version\":\"recovery-engine-benchmark/v1\",'
        '\"cases\":[{\"case_id\":\"x\",\"stratum\":\"SYNTHETIC_MECHANICS\",'
        f'\"setup\":{forbidden_setup},'
        '\"ground_truth\":{\"opportunity_id\":\"opp\",\"obligation_id\":\"obl\",'
        '\"required_evidence_ids\":[]}}]}' ,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="recorded predictions"):
        load_engine_benchmark(manifest)


def test_engine_benchmark_rejects_unsupported_setup_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "engine.json"
    manifest.write_text(
        '{"schema_version":"recovery-engine-benchmark/v1","cases":['
        '{"case_id":"x","stratum":"SYNTHETIC_MECHANICS",'
        '"setup":{"unconsumed":true},'
        '"ground_truth":{"opportunity_id":"opp","obligation_id":"obl",'
        '"required_evidence_ids":[]}}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported benchmark setup fields"):
        load_engine_benchmark(manifest)


@pytest.mark.parametrize(
    ("cutoffs", "label"),
    (
        ({"settlement_cutoff": None}, "settlement_cutoff"),
        ({"pursuit_cutoff": None}, "pursuit_cutoff"),
        ({"settlement_cutoff": None, "pursuit_cutoff": None}, "settlement_cutoff"),
    ),
)
def test_engine_benchmark_rejects_explicit_null_reconciliation_cutoff(
    tmp_path: Path, cutoffs: dict[str, object], label: str
) -> None:
    manifest = tmp_path / "engine.json"
    setup = ",".join(f'\"{field}\":null' for field in cutoffs)
    manifest.write_text(
        "{"
        '\"schema_version\":\"recovery-engine-benchmark/v1\",'
        '\"cases\":[{\"case_id\":\"x\",\"stratum\":\"SYNTHETIC_MECHANICS\",'
        f'\"setup\":{{{setup}}},'
        '\"ground_truth\":{\"opportunity_id\":\"opp\",\"obligation_id\":\"obl\",'
        '\"required_evidence_ids\":[]}}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=label):
        load_engine_benchmark(manifest)


def test_report_renders_execution_provenance_from_one_result_model(tmp_path: Path) -> None:
    report = {
        "benchmark": "engine", "runner_mode": "real_engine",
        "engine_benchmarked_revision": "a" * 40, "results": metrics([_case()]),
        "executions": [{
            "case_id": "positive", "recommendation": "SYNTHETIC_CLAIM_READY",
            "basis": "INVALID_FEE", "stratum": "SYNTHETIC_MECHANICS",
            "currency": "USD", "amount_minor": 200, "evidence": ["E-FEE-VALID"],
        }],
    }
    json_path, markdown_path = tmp_path / "result.json", tmp_path / "result.md"
    write_report(report, json_path, markdown_path)
    assert json_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Runner mode: `real_engine`" in markdown
    assert f"Engine-benchmarked revision: `{'a' * 40}`" in markdown
    assert "basis=INVALID_FEE" in markdown
    assert "stratum=SYNTHETIC_MECHANICS" in markdown
