"""Unit tests for the run state machine (``docs/agent-state-machine.md``).

These assert the transition table against the ``RunStatus`` enum itself rather
than a hand-written list, so adding a state without deciding its edges fails a
test instead of silently widening the machine. Every test that walks the table
derives its cases from ``RunStatus``/``ALLOWED_TRANSITIONS`` for the same reason.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from opspilot.agents.runtime import transition_or_raise
from opspilot.domain.errors import IllegalTransition
from opspilot.domain.runs import ALLOWED_TRANSITIONS, TERMINAL, AgentRun, RunStatus


def _run(status: RunStatus = RunStatus.RECEIVED) -> AgentRun:
    """A minimal run parked at ``status`` (built by setting the field directly)."""
    return AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        status=status,
        model_provider="fake",
        model_name="fake",
    )


def _all_states() -> list[RunStatus]:
    """Every ``RunStatus``, in a stable order, typed for parametrization."""
    return sorted(RunStatus, key=lambda s: s.value)


def test_every_state_appears_as_a_key() -> None:
    """Every ``RunStatus`` member has an entry in the transition table.

    Iterating the enum -- not a literal list -- is the point: a new state added
    without deciding its successors fails here rather than defaulting to "no
    outgoing edges" silently at runtime.
    """
    assert set(ALLOWED_TRANSITIONS) == set(RunStatus)


def test_every_state_has_a_failed_edge_or_is_terminal() -> None:
    """``FAILED`` is reachable from every non-terminal state (§3)."""
    for status in RunStatus:
        if status in TERMINAL:
            continue
        assert RunStatus.FAILED in ALLOWED_TRANSITIONS[status], (
            f"{status.value} cannot reach FAILED"
        )


def test_terminal_states_have_empty_successor_sets() -> None:
    """``COMPLETED`` and ``FAILED`` are terminal: no outgoing edges."""
    assert ALLOWED_TRANSITIONS[RunStatus.COMPLETED] == frozenset()
    assert ALLOWED_TRANSITIONS[RunStatus.FAILED] == frozenset()
    assert frozenset({RunStatus.COMPLETED, RunStatus.FAILED}) == TERMINAL


def test_completed_to_executing_raises() -> None:
    """``COMPLETED -> EXECUTING`` is illegal (``agent-state-machine.md`` §2.1).

    A completed run must never touch a tool again after its outcome was
    reported. Driven through ``transition_to`` -- the single implementation.
    """
    run = _run(RunStatus.COMPLETED)
    with pytest.raises(IllegalTransition) as excinfo:
        run.transition_to(RunStatus.EXECUTING)
    assert excinfo.value.current is RunStatus.COMPLETED
    assert excinfo.value.requested is RunStatus.EXECUTING
    # The failed transition leaves the status untouched.
    assert run.status is RunStatus.COMPLETED


def test_waiting_approval_to_responding_is_legal() -> None:
    """A rejection completes a run: ``WAITING_APPROVAL -> RESPONDING`` (§3).

    The edge is the difference between modelling an approval as a gate and
    modelling it as an error -- a declined refund is a legal workflow outcome,
    not a ``FAILED``.
    """
    run = _run(RunStatus.WAITING_APPROVAL)
    run.transition_to(RunStatus.RESPONDING)
    assert run.status is RunStatus.RESPONDING


@pytest.mark.parametrize("current", _all_states())
def test_every_table_edge_is_reachable(current: RunStatus) -> None:
    """Every edge named in the table is actually taken by ``transition_to``."""
    for allowed in sorted(ALLOWED_TRANSITIONS[current], key=lambda s: s.value):
        run = _run(current)
        assert run.can_transition_to(allowed) is True
        assert run.transition_to(allowed) is run
        assert run.status is allowed


@pytest.mark.parametrize("current", _all_states())
def test_every_non_edge_raises(current: RunStatus) -> None:
    """Every transition *not* in the table raises ``IllegalTransition``.

    Derived by set difference against the enum, so a widened edge is caught as
    surely as a narrowed one.
    """
    for target in RunStatus:
        if target in ALLOWED_TRANSITIONS[current]:
            continue
        run = _run(current)
        assert run.can_transition_to(target) is False
        with pytest.raises(IllegalTransition):
            run.transition_to(target)
        assert run.status is current


def test_executing_self_loop_is_legal() -> None:
    """``EXECUTING -> EXECUTING`` is the multi-tool golden path (§2.1)."""
    run = _run(RunStatus.EXECUTING)
    assert run.can_transition_to(RunStatus.EXECUTING) is True
    run.transition_to(RunStatus.EXECUTING)
    assert run.status is RunStatus.EXECUTING


def test_planning_cannot_reach_a_tool_without_executing() -> None:
    """There is no ``PLANNING -> <tool>`` shortcut: ``EXECUTING`` is the gate (§2.1)."""
    assert RunStatus.EXECUTING in ALLOWED_TRANSITIONS[RunStatus.PLANNING]
    assert RunStatus.WAITING_APPROVAL not in ALLOWED_TRANSITIONS[RunStatus.PLANNING]


def test_transition_or_raise_delegates_to_transition_to() -> None:
    """``runtime.transition_or_raise`` is a delegate, not a second implementation.

    The security test in ``tests/security/test_approval_bypass.py`` calls this
    name; it must produce the same error and the same mutation as
    ``AgentRun.transition_to``.
    """
    legal = _run(RunStatus.EXECUTING)
    transition_or_raise(legal, RunStatus.WAITING_APPROVAL)
    assert legal.status is RunStatus.WAITING_APPROVAL

    illegal = _run(RunStatus.COMPLETED)
    with pytest.raises(IllegalTransition):
        transition_or_raise(illegal, RunStatus.EXECUTING)
    assert illegal.status is RunStatus.COMPLETED


def test_transition_to_returns_the_run_for_chaining() -> None:
    """The run is returned so a caller can chain ``.transition_to(...)``."""
    run = _run(RunStatus.RECEIVED)
    assert run.transition_to(RunStatus.CLASSIFYING) is run


def test_run_status_has_no_setter_api_beyond_transition_to() -> None:
    """There is no ``set_status``-style method on the domain object.

    The rule is only trustworthy if the sole mutation path is ``transition_to``
    (or direct field assignment, which the Pydantic model necessarily allows and
    which the persistence adapter uses when rehydrating a row).
    """
    assert not hasattr(AgentRun, "set_status")
    assert not hasattr(AgentRun, "force_status")
