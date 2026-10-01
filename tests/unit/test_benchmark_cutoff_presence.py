"""Typed benchmark construction must retain cutoff presence semantics."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from recovery_manager.benchmark_provisioning import SyntheticRecoverySetup


def test_omitted_cutoffs_are_valid_fixture_defaults() -> None:
    SyntheticRecoverySetup()


@pytest.mark.parametrize("field", ("settlement_cutoff", "pursuit_cutoff"))
def test_constructor_rejects_explicit_null_cutoff(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        SyntheticRecoverySetup(**{field: None})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ("settlement_cutoff", "pursuit_cutoff"))
def test_replace_rejects_explicit_null_cutoff(field: str) -> None:
    base = SyntheticRecoverySetup(**{field: datetime(2020, 1, 1, tzinfo=UTC)})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=field):
        replace(base, **{field: None})


def test_both_explicit_null_cutoffs_are_rejected() -> None:
    with pytest.raises(ValueError, match="settlement_cutoff"):
        SyntheticRecoverySetup(settlement_cutoff=None, pursuit_cutoff=None)


def test_explicit_null_is_rejected_before_adapter_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import recovery_manager.engine_benchmark as module

    provision_called = False

    def unexpected_provision(*args: object, **kwargs: object) -> None:
        nonlocal provision_called
        provision_called = True
        raise AssertionError("explicit null reached provisioning")

    monkeypatch.setattr(module, "provision_synthetic_recovery_case", unexpected_provision)
    with pytest.raises(ValueError, match="settlement_cutoff"):
        SyntheticRecoverySetup(settlement_cutoff=None)
    assert not provision_called


def test_manifest_omission_and_explicit_null_take_distinct_paths() -> None:
    SyntheticRecoverySetup.from_manifest({})
    for field in ("settlement_cutoff", "pursuit_cutoff"):
        with pytest.raises(ValueError, match=field):
            SyntheticRecoverySetup.from_manifest({field: None})
