from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from recovery_manager.canonical import EXPECTED_FIXTURE_COUNTS, canonicalize_fixture
from recovery_manager.db import set_local_tenant
from recovery_manager.models import EvidenceRecord, FinancialEvent, SourceRecordVersion


def test_canonical_fixture_import_is_tenant_scoped_idempotent_and_reconciled(
    runtime_factory, settings
) -> None:
    demo = settings.model_copy(update={"demo_fixtures_enabled": True})
    paths = [
        Path("data/fee_report_sample.csv"),
        Path("data/upstream/receiving_sample.csv"),
        Path("data/upstream/prep_sample.csv"),
        Path("data/upstream/pack_sample.csv"),
        Path("data/upstream/returns_sample.csv"),
    ]
    assert sum(canonicalize_fixture(runtime_factory, demo, path) for path in paths) == sum(
        EXPECTED_FIXTURE_COUNTS.values()
    )
    assert sum(canonicalize_fixture(runtime_factory, demo, path) for path in paths) == 0
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        assert session.execute(select(func.count(SourceRecordVersion.id))).scalar_one() > 0
        assert session.execute(select(func.count(FinancialEvent.id))).scalar_one() > 0
        assert session.execute(select(func.count(EvidenceRecord.id))).scalar_one() > 0
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_bravo")
        assert session.execute(select(func.count(SourceRecordVersion.id))).scalar_one() > 0
