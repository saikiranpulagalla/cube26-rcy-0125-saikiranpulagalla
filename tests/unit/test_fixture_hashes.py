from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from recovery_manager.fixtures import KNOWN_FIXTURE_HASHES


def test_known_fixture_allowlist_matches_all_preserved_organizer_files() -> None:
    paths = (
        Path("data/fee_report_sample.csv"),
        *sorted(Path("data/upstream").glob("*.csv")),
    )
    assert len(paths) == 5
    assert {sha256(path.read_bytes()).hexdigest() for path in paths} == KNOWN_FIXTURE_HASHES
