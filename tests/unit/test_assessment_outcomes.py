from recovery_manager.assessment import derived_recommendation
from recovery_manager.ledger import Residual


def _residual(settlement: int, pursuit: int, remaining: int | None) -> Residual:
    return Residual("USD", 200, settlement, pursuit, remaining, None)


def test_derived_synthetic_outcomes_are_exact_and_conflicts_are_review() -> None:
    assert derived_recommendation(_residual(0, 0, 200), True) == ("SYNTHETIC_CLAIM_READY", 200)
    assert derived_recommendation(_residual(100, 0, 100), True) == ("SYNTHETIC_CLAIM_READY", 100)
    assert derived_recommendation(_residual(200, 0, 0), True) == ("RESOLVED", None)
    assert derived_recommendation(_residual(0, 200, 0), True) == ("ALREADY_PURSUED", None)
    assert derived_recommendation(_residual(0, 100, 100), True) == ("SYNTHETIC_CLAIM_READY", 100)
    assert derived_recommendation(_residual(201, 0, None), True) == ("REVIEW", None)
    assert derived_recommendation(_residual(0, 201, None), True) == ("REVIEW", None)
    assert derived_recommendation(_residual(0, 0, 200), False) == ("REVIEW", None)
