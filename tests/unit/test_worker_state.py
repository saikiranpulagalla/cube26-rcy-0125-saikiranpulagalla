from __future__ import annotations

from recovery_manager.models import ExecutionState
from recovery_manager.worker import transition_is_legal


def test_only_approved_execution_transitions_are_legal() -> None:
    assert transition_is_legal(ExecutionState.QUEUED, ExecutionState.RUNNING)
    assert transition_is_legal(ExecutionState.RUNNING, ExecutionState.COMPLETED)
    assert transition_is_legal(ExecutionState.RUNNING, ExecutionState.RETRYABLE_FAILURE)
    assert not transition_is_legal(ExecutionState.QUEUED, ExecutionState.COMPLETED)
    assert not transition_is_legal(ExecutionState.COMPLETED, ExecutionState.RUNNING)
    assert not transition_is_legal(ExecutionState.TERMINAL_FAILURE, ExecutionState.QUEUED)
