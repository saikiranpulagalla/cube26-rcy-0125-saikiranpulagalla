from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import ProgrammingError

from recovery_manager.db import set_local_tenant
from recovery_manager.fixtures import load_known_fixture
from recovery_manager.ingestion import accept_input
from recovery_manager.models import RawEnvelope, WorkIntent


def test_concurrent_same_key_and_bytes_reuses_one_durable_acceptance(
    runtime_factory, alpha, settings
) -> None:
    barrier = Barrier(2)

    def submit():
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, alpha.org_id)
            barrier.wait(timeout=5)
            return accept_input(
                session,
                alpha,
                b"concurrent exact bytes",
                "application/octet-stream",
                "concurrent.bin",
                "concurrent-key",
                settings,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))

    assert {result.envelope_id for result in results}.__len__() == 1
    assert {result.work_id for result in results}.__len__() == 1
    assert sum(result.replayed for result in results) == 1
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert session.execute(select(RawEnvelope)).scalars().all().__len__() == 1
        assert session.execute(select(WorkIntent)).scalars().all().__len__() == 1


def test_fixture_loader_is_disabled_without_explicit_demo_mode(runtime_factory, settings) -> None:
    with pytest.raises(PermissionError, match="disabled"):
        load_known_fixture(runtime_factory, settings, Path("data/fee_report_sample.csv"))


def test_allowlisted_fixture_loader_preserves_original_file_and_row_provenance(
    runtime_factory, settings
) -> None:
    demo_settings = settings.model_copy(update={"demo_fixtures_enabled": True})
    results = load_known_fixture(runtime_factory, demo_settings, Path("data/fee_report_sample.csv"))
    assert results
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        alpha_envelopes = session.execute(select(RawEnvelope)).scalars().all()
        assert alpha_envelopes
        provenance = alpha_envelopes[0].fixture_provenance
        assert provenance is not None
        assert provenance["fixture"] is True
        assert provenance["declared_org_id"] == "org_demo_alpha"
        assert isinstance(provenance["original_row_number"], int)


def test_rls_blocks_cross_tenant_update_and_delete(runtime_factory, alpha, bravo, settings) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, bravo.org_id)
        created = accept_input(
            session,
            bravo,
            b"bravo only",
            "application/octet-stream",
            "bravo.bin",
            "bravo-mutation-key",
            settings,
        )
    with pytest.raises(ProgrammingError):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, alpha.org_id)
            session.execute(
                update(RawEnvelope)
                .where(RawEnvelope.id == created.envelope_id)
                .values(source_name="forbidden")
            )
    with pytest.raises(ProgrammingError):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, alpha.org_id)
            session.execute(delete(RawEnvelope).where(RawEnvelope.id == created.envelope_id))
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, bravo.org_id)
        assert session.get(RawEnvelope, created.envelope_id) is not None
