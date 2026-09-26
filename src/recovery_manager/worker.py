from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings
from recovery_manager.db import set_local_tenant
from recovery_manager.models import AuditEvent, ExecutionState, WorkAttempt, WorkIntent

LEGAL_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.QUEUED: frozenset({ExecutionState.RUNNING}),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.COMPLETED,
            ExecutionState.RETRYABLE_FAILURE,
            ExecutionState.TERMINAL_FAILURE,
        }
    ),
    ExecutionState.RETRYABLE_FAILURE: frozenset(
        {ExecutionState.QUEUED, ExecutionState.TERMINAL_FAILURE}
    ),
    ExecutionState.TERMINAL_FAILURE: frozenset(),
    ExecutionState.COMPLETED: frozenset(),
}


class StaleLeaseError(RuntimeError):
    pass


def transition_is_legal(old: ExecutionState, new: ExecutionState) -> bool:
    return new in LEGAL_TRANSITIONS[old]


class PollingWorker:
    def __init__(self, factory: sessionmaker[Session], settings: Settings, owner: str) -> None:
        self.factory = factory
        self.settings = settings
        self.owner = owner

    def recover_expired_and_acquire(self, org_id: str) -> tuple[UUID, UUID] | None:
        with self.factory() as session, session.begin():
            set_local_tenant(session, org_id)
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            expired = (
                session.execute(
                    select(WorkIntent)
                    .where(
                        WorkIntent.org_id == org_id,
                        WorkIntent.state == ExecutionState.RUNNING.value,
                        WorkIntent.lease_expires_at < now,
                    )
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            for work in expired:
                expired_token = work.lease_token
                work.state = ExecutionState.RETRYABLE_FAILURE.value
                work.last_error = "Lease expired before completion"
                work.lease_owner = None
                work.lease_token = None
                work.lease_expires_at = None
                work.next_run_at = now
                attempt = session.execute(
                    select(WorkAttempt).where(
                        WorkAttempt.org_id == org_id,
                        WorkAttempt.work_intent_id == work.id,
                        WorkAttempt.lease_token == expired_token,
                    )
                ).scalar_one()
                attempt.finished_at = now
                attempt.outcome = "LEASE_EXPIRED"
                session.add(
                    AuditEvent(
                        org_id=org_id,
                        actor_id=None,
                        event_type="LEASE_EXPIRED",
                        subject_type="work_intent",
                        subject_id=str(work.id),
                        details={},
                    )
                )
            session.flush()
            retryable = (
                session.execute(
                    select(WorkIntent)
                    .where(
                        WorkIntent.org_id == org_id,
                        WorkIntent.state == ExecutionState.RETRYABLE_FAILURE.value,
                        WorkIntent.next_run_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            for work in retryable:
                if work.attempt_count >= work.max_attempts:
                    work.state = ExecutionState.TERMINAL_FAILURE.value
                else:
                    work.state = ExecutionState.QUEUED.value
            session.flush()
            work = session.execute(
                select(WorkIntent)
                .where(
                    WorkIntent.org_id == org_id,
                    WorkIntent.state == ExecutionState.QUEUED.value,
                    WorkIntent.next_run_at <= now,
                )
                .order_by(WorkIntent.received_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if work is None:
                return None
            token = uuid4()
            work.state = ExecutionState.RUNNING.value
            work.attempt_count += 1
            work.lease_owner = self.owner
            work.lease_token = token
            work.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            session.add(
                WorkAttempt(
                    org_id=org_id,
                    work_intent_id=work.id,
                    attempt_number=work.attempt_count,
                    lease_owner=self.owner,
                    lease_token=token,
                )
            )
            return work.id, token

    def complete(self, org_id: str, work_id: UUID, lease_token: UUID) -> None:
        with self.factory() as session, session.begin():
            set_local_tenant(session, org_id)
            work = session.execute(
                select(WorkIntent)
                .where(WorkIntent.org_id == org_id, WorkIntent.id == work_id)
                .with_for_update()
            ).scalar_one_or_none()
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            if (
                work is None
                or work.state != ExecutionState.RUNNING.value
                or work.lease_token != lease_token
                or work.lease_expires_at is None
                or work.lease_expires_at <= now
            ):
                raise StaleLeaseError("Worker no longer owns an active lease")
            work.state = ExecutionState.COMPLETED.value
            work.completed_at = now
            work.lease_owner = None
            work.lease_token = None
            work.lease_expires_at = None
            attempt = session.execute(
                select(WorkAttempt).where(
                    WorkAttempt.org_id == org_id,
                    WorkAttempt.work_intent_id == work_id,
                    WorkAttempt.lease_token == lease_token,
                )
            ).scalar_one()
            attempt.finished_at = now
            attempt.outcome = "COMPLETED_STRUCTURAL_PROCESSING"
            session.add(
                AuditEvent(
                    org_id=org_id,
                    actor_id=None,
                    event_type="COMPLETED_STRUCTURAL_PROCESSING",
                    subject_type="work_intent",
                    subject_id=str(work_id),
                    details={},
                )
            )

    def fail(
        self, org_id: str, work_id: UUID, lease_token: UUID, detail: str, retryable: bool = True
    ) -> None:
        with self.factory() as session, session.begin():
            set_local_tenant(session, org_id)
            work = session.execute(
                select(WorkIntent)
                .where(WorkIntent.org_id == org_id, WorkIntent.id == work_id)
                .with_for_update()
            ).scalar_one_or_none()
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            if (
                work is None
                or work.lease_token != lease_token
                or work.state != ExecutionState.RUNNING.value
                or work.lease_expires_at is None
                or work.lease_expires_at <= now
            ):
                raise StaleLeaseError("Worker no longer owns this lease")
            terminal = not retryable or work.attempt_count >= work.max_attempts
            work.state = (
                ExecutionState.TERMINAL_FAILURE.value
                if terminal
                else ExecutionState.RETRYABLE_FAILURE.value
            )
            work.last_error = detail
            work.next_run_at = now
            work.lease_owner = None
            work.lease_token = None
            work.lease_expires_at = None
            attempt = session.execute(
                select(WorkAttempt).where(
                    WorkAttempt.org_id == org_id,
                    WorkAttempt.work_intent_id == work_id,
                    WorkAttempt.lease_token == lease_token,
                )
            ).scalar_one()
            attempt.finished_at = now
            attempt.outcome = work.state
            attempt.detail = detail

    def run_once(self, org_id: str) -> UUID | None:
        lease = self.recover_expired_and_acquire(org_id)
        if lease is None:
            return None
        work_id, token = lease
        self.complete(org_id, work_id, token)
        return work_id
