"""Frozen, evidence-aware evaluation of Recovery decisions."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

READY = "SYNTHETIC_CLAIM_READY"
DETERMINATE = frozenset({READY, "RESOLVED", "ALREADY_PURSUED", "NO_CLAIM"})


@dataclass(frozen=True)
class EvaluationCase:
    scenario_id: str
    dependency_group: str
    stratum: str
    expected_recommendation: str
    expected_amount_minor: int | None
    predicted_recommendation: str
    predicted_amount_minor: int | None
    # Kept for source compatibility only. Metrics use referenced evidence edges.
    evidence_sufficient: bool = False
    evidence_attribution_correct: bool = False
    expected_opportunity_id: str = ""
    predicted_opportunity_id: str = ""
    expected_obligation_id: str | None = None
    predicted_obligation_id: str | None = None
    expected_basis: str = ""
    predicted_basis: str = ""
    expected_currency: str | None = None
    predicted_currency: str | None = None
    required_evidence: frozenset[str] = field(default_factory=frozenset)
    predicted_evidence: frozenset[str] = field(default_factory=frozenset)
    dependency_ids: tuple[str, ...] = ()

    def split(self) -> str:
        return self.scenario_id.split("/", 1)[0]

    def opportunity(self) -> str:
        return self.expected_opportunity_id or self.scenario_id


def validate_split(cases: list[EvaluationCase]) -> None:
    groups: dict[str, set[str]] = {}
    for case in cases:
        for group in {case.dependency_group, case.opportunity(), *case.dependency_ids}:
            groups.setdefault(group, set()).add(case.split())
    leaked = [group for group, splits in groups.items() if len(splits) > 1]
    if leaked:
        raise ValueError(f"dependency groups span splits: {', '.join(sorted(leaked))}")


def _strict(prediction: EvaluationCase, truth: EvaluationCase) -> bool:
    return (
        prediction.predicted_recommendation == truth.expected_recommendation == READY
        and prediction.predicted_opportunity_id == truth.expected_opportunity_id
        and prediction.predicted_obligation_id == truth.expected_obligation_id
        and prediction.predicted_basis == truth.expected_basis
        and prediction.predicted_currency == truth.expected_currency
        and prediction.predicted_amount_minor == truth.expected_amount_minor
        and prediction.predicted_evidence == truth.required_evidence
    )


def _score(cases: list[EvaluationCase]) -> dict[str, Any]:
    truth: dict[str, EvaluationCase] = {}
    for case in cases:
        prior = truth.get(case.opportunity())
        if prior is not None and (
            prior.expected_recommendation, prior.expected_amount_minor, prior.expected_basis,
            prior.expected_currency, prior.required_evidence,
        ) != (
            case.expected_recommendation, case.expected_amount_minor, case.expected_basis,
            case.expected_currency, case.required_evidence,
        ):
            raise ValueError(f"conflicting ground truth for opportunity {case.opportunity()}")
        truth[case.opportunity()] = case
    predictions = [case for case in cases if case.predicted_recommendation == READY]
    gold = [case for case in truth.values() if case.expected_recommendation == READY]
    matched: set[str] = set()
    true_positive: list[EvaluationCase] = []
    for prediction in sorted(predictions, key=lambda item: item.scenario_id):
        candidate = truth.get(prediction.predicted_opportunity_id)
        if candidate is not None and candidate.opportunity() not in matched and _strict(prediction, candidate):
            matched.add(candidate.opportunity())
            true_positive.append(prediction)
    false_exposure: Counter[str] = Counter()
    for prediction in predictions:
        if prediction not in true_positive:
            false_exposure[prediction.predicted_currency or "UNSPECIFIED"] += max(prediction.predicted_amount_minor or 0, 0)
    predicted_edges = [edge for case in predictions for edge in case.predicted_evidence]
    relevant_edges = sum(edge in case.required_evidence for case in predictions for edge in case.predicted_evidence)
    total = len(cases)
    return {
        "cases": total,
        "claims_recommended": len(predictions),
        "strict_true_positives": len(true_positive),
        "strict_claim_precision": None if not predictions else len(true_positive) / len(predictions),
        "strict_claim_recall": None if not gold else len(true_positive) / len(gold),
        "exact_amount_accuracy": None if not predictions else len(true_positive) / len(predictions),
        "evidence_attribution_precision": None if not predicted_edges else relevant_edges / len(predicted_edges),
        "evidence_sufficiency_rate": None if not predictions else sum(case.required_evidence <= case.predicted_evidence for case in predictions) / len(predictions),
        "decision_coverage": None if not total else sum(case.predicted_recommendation in DETERMINATE for case in cases) / total,
        "review_rate": None if not total else sum(case.predicted_recommendation == "REVIEW" for case in cases) / total,
        "false_exposure_minor": dict(false_exposure),
    }


def metrics(cases: list[EvaluationCase]) -> dict[str, Any]:
    """Strict one-to-one claim metrics, separated by authority stratum."""
    validate_split(cases)
    result = _score(cases)
    result["strata"] = {
        name: _score([case for case in cases if case.stratum == name])
        for name in sorted({case.stratum for case in cases})
    }
    return result


def load_manifest(path: Path) -> list[EvaluationCase]:
    """Load a locked manifest; an adapter supplies real-engine predictions."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("evaluation manifest requires a cases list")
    cases: list[EvaluationCase] = []
    for item in raw["cases"]:
        if not isinstance(item, dict):
            raise ValueError("evaluation case must be an object")
        values = dict(item)
        values["required_evidence"] = frozenset(values.get("required_evidence", []))
        values["predicted_evidence"] = frozenset(values.get("predicted_evidence", []))
        values["dependency_ids"] = tuple(values.get("dependency_ids", []))
        cases.append(EvaluationCase(**values))
    return cases


def run_manifest(path: Path, execute: Callable[[EvaluationCase], EvaluationCase]) -> dict[str, Any]:
    """Score results from an engine adapter; this function never manufactures predictions."""
    cases = [execute(case) for case in load_manifest(path)]
    return {"benchmark": path.name, "results": metrics(cases), "cases": [asdict(case) for case in cases]}


def write_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    """Write deterministic machine and human-readable evaluation artifacts."""
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=list) + "\n", encoding="utf-8")
    result = report["results"]
    markdown_path.write_text(
        "\n".join((
            "# Recovery evaluation report", "", f"Benchmark: `{report['benchmark']}`",
            f"Cases: {result['cases']}", f"Strict claim precision: {result['strict_claim_precision']}",
            f"Decision coverage: {result['decision_coverage']}",
            f"Unsupported exposure by currency: {result['false_exposure_minor']}", "",
            "Synthetic-mechanics results validate the deterministic synthetic contract only.",
            "Operational recovery-policy accuracy is not measured because authoritative operational policy is unavailable.",
        )) + "\n",
        encoding="utf-8",
    )
