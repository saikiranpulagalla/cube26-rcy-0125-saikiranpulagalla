from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import ExecutionState, WorkIntent
from recovery_manager.worker import PollingWorker, StaleLeaseError


def _queued(factory, alpha, settings):
    with factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        return accept_input(
            session, alpha, b"queued", "application/octet-stream", "x", "worker-key", settings
        )


def test_worker_completes_structural_work_after_committed_acceptance(
    runtime_factory, alpha, settings
) -> None:
    result = _queued(runtime_factory, alpha, settings)
    worker = PollingWorker(runtime_factory, settings, "worker-a")
    assert worker.run_once(alpha.org_id) == result.work_id
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert session.get(WorkIntent, result.work_id).state == ExecutionState.COMPLETED.value


def test_expired_lease_is_recovered_and_stale_worker_cannot_complete(
    runtime_factory, alpha, settings
) -> None:
    _queued(runtime_factory, alpha, settings)
    first = PollingWorker(runtime_factory, settings, "first")
    acquired = first.recover_expired_and_acquire(alpha.org_id)
    assert acquired is not None
    work_id, old_token = acquired
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        work = session.get(WorkIntent, work_id)
        assert work is not None
        work.lease_expires_at = session.execute(select(func.now())).scalar_one() - timedelta(seconds=1)
    second = PollingWorker(runtime_factory, settings, "second")
    acquired_second = second.recover_expired_and_acquire(alpha.org_id)
    assert acquired_second is not None
    assert acquired_second[0] == work_id
    with pytest.raises(StaleLeaseError):
        first.complete(alpha.org_id, work_id, old_token)
    second.complete(alpha.org_id, work_id, acquired_second[1])


def test_retry_is_bounded(runtime_factory, alpha, settings) -> None:
    result = _queued(runtime_factory, alpha, settings)
    worker = PollingWorker(runtime_factory, settings, "worker")
    lease = worker.recover_expired_and_acquire(alpha.org_id)
    assert lease is not None
    worker.fail(alpha.org_id, lease[0], lease[1], "simulated", retryable=False)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert (
            session.get(WorkIntent, result.work_id).state == ExecutionState.TERMINAL_FAILURE.value
        )
