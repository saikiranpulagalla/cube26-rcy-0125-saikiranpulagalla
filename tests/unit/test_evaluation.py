import pytest

from recovery_manager.evaluation import EvaluationCase, metrics, validate_split


def test_metrics_keep_zero_prediction_precision_na_and_strata_separate() -> None:
    cases = [
        EvaluationCase("dev/missing-evidence", "g1", "POLICY_UNAVAILABLE", "REVIEW", None, "REVIEW", None, False, False),
        EvaluationCase("dev/synthetic", "g2", "SYNTHETIC_MECHANICS", "SYNTHETIC_CLAIM_READY", 200, "REVIEW", None, True, True),
    ]
    result = metrics(cases)
    assert result["strict_claim_precision"] is None
    assert result["strata"] == {"POLICY_UNAVAILABLE": 1, "SYNTHETIC_MECHANICS": 1}


def test_dependency_groups_cannot_leak_between_splits() -> None:
    cases = [
        EvaluationCase("dev/a", "shared", "SYNTHETIC_MECHANICS", "REVIEW", None, "REVIEW", None, True, True),
        EvaluationCase("validation/b", "shared", "SYNTHETIC_MECHANICS", "REVIEW", None, "REVIEW", None, True, True),
    ]
    with pytest.raises(ValueError, match="dependency groups"):
        validate_split(cases)
