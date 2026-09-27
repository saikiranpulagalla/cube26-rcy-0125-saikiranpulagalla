"""Frozen, synthetic-safe evaluation primitives for v0.7."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvaluationCase:
    scenario_id: str
    dependency_group: str
    stratum: str
    expected_recommendation: str
    expected_amount_minor: int | None
    predicted_recommendation: str
    predicted_amount_minor: int | None
    evidence_sufficient: bool
    evidence_attribution_correct: bool


def validate_split(cases: list[EvaluationCase]) -> None:
    groups: dict[str, set[str]] = {}
    for case in cases:
        split = case.scenario_id.split("/", 1)[0]
        groups.setdefault(case.dependency_group, set()).add(split)
    leaked = [group for group, splits in groups.items() if len(splits) > 1]
    if leaked:
        raise ValueError(f"dependency groups span splits: {', '.join(sorted(leaked))}")


def metrics(cases: list[EvaluationCase]) -> dict[str, Any]:
    ready = "SYNTHETIC_CLAIM_READY"
    predicted_ready = [case for case in cases if case.predicted_recommendation == ready]
    gold_ready = [case for case in cases if case.expected_recommendation == ready]
    true_positive = [
        case
        for case in predicted_ready
        if case.expected_recommendation == ready
        and case.predicted_amount_minor == case.expected_amount_minor
    ]
    false_exposure = Counter(
        {
            "USD": sum(
                max(case.predicted_amount_minor or 0, 0)
                for case in predicted_ready
                if case.expected_recommendation != ready
            )
        }
    )
    overclaim = Counter(
        {
            "USD": sum(
                max((case.predicted_amount_minor or 0) - (case.expected_amount_minor or 0), 0)
                for case in predicted_ready
                if case.expected_recommendation == ready
            )
        }
    )
    return {
        "strict_claim_precision": None if not predicted_ready else len(true_positive) / len(predicted_ready),
        "strict_claim_recall": None if not gold_ready else len(true_positive) / len(gold_ready),
        "exact_amount_accuracy": None if not predicted_ready else len(true_positive) / len(predicted_ready),
        "evidence_attribution_precision": None
        if not predicted_ready
        else sum(case.evidence_attribution_correct for case in predicted_ready) / len(predicted_ready),
        "evidence_sufficiency_rate": sum(case.evidence_sufficient for case in cases) / len(cases),
        "decision_coverage": sum(case.predicted_recommendation != "SILENT" for case in cases) / len(cases),
        "review_rate": sum(case.predicted_recommendation == "REVIEW" for case in cases) / len(cases),
        "policy_unavailable_rate": sum(case.stratum == "POLICY_UNAVAILABLE" for case in cases) / len(cases),
        "false_exposure_minor": dict(false_exposure),
        "overclaim_exposure_minor": dict(overclaim),
        "strata": dict(Counter(case.stratum for case in cases)),
    }
