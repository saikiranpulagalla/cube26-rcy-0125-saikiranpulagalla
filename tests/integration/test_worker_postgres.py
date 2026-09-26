from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from time import sleep

import pytest
from sqlalchemy import select

from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import ExecutionState, WorkAttempt, WorkIntent
from recovery_manager.worker import PollingWorker, StaleLeaseError


def _queued(factory, alpha, settings):
    with factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        return accept_input(
            session, alpha, b"queued", "application/octet-stream", "x", "worker-key", settings
        )


def test_worker_completes_structural_work_after_committed_acceptance(
    runtime_factory, worker_factory, alpha, settings
) -> None:
    result = _queued(runtime_factory, alpha, settings)
    worker = PollingWorker(worker_factory, settings, "worker-a")
    assert worker.run_once(alpha.org_id) == result.work_id
    with worker_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert session.get(WorkIntent, result.work_id).state == ExecutionState.COMPLETED.value


def test_expired_lease_is_recovered_and_stale_worker_cannot_complete(
    runtime_factory, worker_factory, alpha, settings
) -> None:
    _queued(runtime_factory, alpha, settings)
    short_lease_settings = settings.model_copy(update={"lease_seconds": 1})
    first = PollingWorker(worker_factory, short_lease_settings, "first")
    acquired = first.recover_expired_and_acquire(alpha.org_id)
    assert acquired is not None
    work_id, old_token = acquired
    sleep(1.1)
    second = PollingWorker(worker_factory, short_lease_settings, "second")
    acquired_second = second.recover_expired_and_acquire(alpha.org_id)
    assert acquired_second is not None
    assert acquired_second[0] == work_id
    with pytest.raises(StaleLeaseError):
        first.complete(alpha.org_id, work_id, old_token)
    second.complete(alpha.org_id, work_id, acquired_second[1])


def test_retry_is_bounded(runtime_factory, worker_factory, alpha, settings) -> None:
    result = _queued(runtime_factory, alpha, settings)
    short_lease_settings = settings.model_copy(update={"lease_seconds": 1})
    worker = PollingWorker(worker_factory, short_lease_settings, "worker")
    lease = worker.recover_expired_and_acquire(alpha.org_id)
    assert lease is not None
    worker.fail(alpha.org_id, lease[0], lease[1], "simulated", retryable=False)
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        assert (
            session.get(WorkIntent, result.work_id).state == ExecutionState.TERMINAL_FAILURE.value
        )


def test_expired_worker_cannot_fail_and_expired_attempt_is_closed(
    runtime_factory, worker_factory, alpha, settings
) -> None:
    _queued(runtime_factory, alpha, settings)
    short_lease_settings = settings.model_copy(update={"lease_seconds": 1})
    worker = PollingWorker(worker_factory, short_lease_settings, "worker")
    lease = worker.recover_expired_and_acquire(alpha.org_id)
    assert lease is not None
    sleep(1.1)
    with pytest.raises(StaleLeaseError):
        worker.fail(alpha.org_id, lease[0], lease[1], "too late")
    assert worker.recover_expired_and_acquire(alpha.org_id) is not None
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, alpha.org_id)
        attempt = session.execute(
            select(WorkAttempt).where(
                WorkAttempt.work_intent_id == lease[0], WorkAttempt.attempt_number == 1
            )
        ).scalar_one()
        assert attempt.outcome == "LEASE_EXPIRED"
        assert attempt.finished_at is not None


def test_completion_rechecks_database_clock_after_lock_wait(
    runtime_factory, worker_factory, alpha, settings
) -> None:
    _queued(runtime_factory, alpha, settings)
    short_lease_settings = settings.model_copy(update={"lease_seconds": 1})
    worker = PollingWorker(worker_factory, short_lease_settings, "worker")
    lease = worker.recover_expired_and_acquire(alpha.org_id)
    assert lease is not None
    locked = Event()
    release = Event()

    def hold_row_lock() -> None:
        with worker_factory() as session, session.begin():
            set_local_tenant(session, alpha.org_id)
            session.execute(select(WorkIntent).where(WorkIntent.id == lease[0]).with_for_update())
            locked.set()
            assert release.wait(timeout=5)

    holder = Thread(target=hold_row_lock)
    holder.start()
    assert locked.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=1) as executor:
        completion = executor.submit(worker.complete, alpha.org_id, lease[0], lease[1])
        sleep(1.1)
        release.set()
        with pytest.raises(StaleLeaseError):
            completion.result(timeout=5)
    holder.join(timeout=5)
    assert not holder.is_alive()
    assert PollingWorker(worker_factory, short_lease_settings, "replacement").recover_expired_and_acquire(
        alpha.org_id
    )
