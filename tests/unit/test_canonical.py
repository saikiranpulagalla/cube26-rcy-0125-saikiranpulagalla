from __future__ import annotations

from pathlib import Path

import pytest

from recovery_manager.canonical import (
    EXPECTED_FIXTURE_COUNTS,
    CanonicalizationError,
    parse_fixture,
    parse_money_minor,
    parse_nonnegative_quantity,
)


@pytest.mark.parametrize(
    ("relative", "kind"),
    [
        ("data/fee_report_sample.csv", "fee_report"),
        ("data/upstream/receiving_sample.csv", "receiving"),
        ("data/upstream/prep_sample.csv", "prep"),
        ("data/upstream/pack_sample.csv", "pack"),
        ("data/upstream/returns_sample.csv", "returns"),
    ],
)
def test_all_allowlisted_fixtures_reconcile_exact_row_counts(relative: str, kind: str) -> None:
    rows = parse_fixture(Path(relative))
    assert len(rows) == EXPECTED_FIXTURE_COUNTS[kind]
    assert {row.source_record_id for row in rows}.__len__() == len(rows)
    assert all(row.org_id == row.payload["org_id"] for row in rows)


@pytest.mark.parametrize("bad", ["1.001", "1e2", " 1.00", "NaN", "Infinity", ""])
def test_money_parser_rejects_coercive_or_ambiguous_values(bad: str) -> None:
    with pytest.raises(CanonicalizationError):
        parse_money_minor(bad)


def test_money_and_quantity_are_exact_and_nonnegative() -> None:
    assert parse_money_minor("4.25") == 425
    assert parse_money_minor("-0.10") == -10
    assert parse_nonnegative_quantity("2.5") == 2.5
    with pytest.raises(CanonicalizationError):
        parse_nonnegative_quantity("-1")


def test_row_order_does_not_change_individual_canonical_identity() -> None:
    rows = parse_fixture(Path("data/fee_report_sample.csv"))
    original = {(row.source_record_id, row.content_sha256) for row in rows}
    reordered = {(row.source_record_id, row.content_sha256) for row in reversed(rows)}
    assert reordered == original
