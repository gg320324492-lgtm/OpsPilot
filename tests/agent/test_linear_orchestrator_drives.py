"""The orchestrator really drives: the pump consults it, proven by a spy.

Why this file exists
--------------------
``docs/milestones.md`` §M4 carries the criterion "``LinearOrchestrator`` drives a
run end to end with the fake provider". The risk this test is written against is
not that the orchestrator is *slightly* wrong — it is that the criterion is
**ticked while ``_pump`` still drives and the orchestrator is passed and
discarded**. That failure mode is the exact defect ``docs/limitations.md`` §8
records three times over: documentation describing a capability no code performs.

So the deciding assertion is **not** "the run completes". A test that only
asserts ``COMPLETED`` passes against an implementation in which the orchestrator
is decorative, because the pump completes the run either way. The assertion here
is that a spy wrapped around the orchestrator **observed the exact stage
sequence** — which can only be true if ``step`` was called, in order, once per
stage, by the thing driving the run.

The spy
-------
``RecordingOrchestrator`` delegates to a real ``LinearOrchestrator`` (so the run
is genuinely driven through the port, not re-implemented in the test) and records
the ``StepKind`` of every ``StepResult`` its ``step`` returns. Its ``bind_driver``
forwarding is load-bearing: ``_pump`` binds the driver duck-typed, and a spy that
dropped the binding would record nothing — which is itself a way to notice the
pump stopped calling ``step``.

What the golden path produces
-----------------------------
Four read/refund tools, then the refund parks the run at gate 5:

    CLASSIFY, RETRIEVE, PLAN, EXECUTE, PLAN, EXECUTE, PLAN, EXECUTE, PLAN,
    EXECUTE, PLAN, RESPOND

with ``EXECUTE`` for ``billing.issue_refund`` raising ``RunParked`` (the run parks
*during* the EXECUTE stage, so the RESPOND stage is on the post-approval drain,
which takes the ``_resume`` path and not the pump). That is why the assertion
below splits the two drains.
"""

from __future__ import annotations

import pytest

from opspilot.adapters.orchestration.linear import LinearOrchestrator
from opspilot.agents.runtime import StageDriver
from opspilot.domain.errors import RunParked
from opspilot.domain.runs import AgentRun
from opspilot.ports.orchestrator import StepKind, StepResult
from tests.agent._golden_harness import Harness


class RecordingOrchestrator:
    """A spy that delegates to ``LinearOrchestrator`` and records every stage.

    It is a wrapper, not a re-implementation: the run it records is driven by the
    real executor's ``step`` (which dispatches to the runtime's stage functions),
    so the recorded sequence is what actually ran. The wrapper is the seam the
    milestone criterion turns on — if ``_pump`` went back to discarding its
    orchestrator, ``kinds`` would be empty and every assertion here would fail.
    """

    def __init__(self) -> None:
        self._inner = LinearOrchestrator()
        self.kinds: list[StepKind] = []
        self.runs_stepped: list[AgentRun] = []

    def bind_driver(self, driver: StageDriver) -> None:
        """Forward the binding, or ``step`` would raise and the run would not drive."""
        self._inner.bind_driver(driver)

    async def step(self, run: AgentRun) -> StepResult:
        """Record the stage that ran, then return its result.

        The kind is read from the returned ``StepResult``. The one stage that
        does **not** return normally is the EXECUTE that parks on the refund:
        ``_park_run`` raises ``RunParked``, so the result never arrives. That case
        is recorded explicitly before the exception is re-raised — the parking
        EXECUTE is part of the sequence and dropping it would hide the very stage
        that proves the run reached the gate.
        """
        self.runs_stepped.append(run)
        try:
            result = await self._inner.step(run)
        except RunParked:
            # Parking happens *during* EXECUTE; the stage ran, so it is recorded.
            self.kinds.append(StepKind.EXECUTE)
            raise
        self.kinds.append(result.kind)
        return result


class _RaisingOrchestrator:
    """An orchestrator whose ``step`` raises, used to prove the pump calls it."""

    async def step(self, run: AgentRun) -> StepResult:
        raise NotImplementedError


async def test_linear_orchestrator_drives_the_golden_path_stage_by_stage(
    harness: Harness,
) -> None:
    """The pump consults the orchestrator: the spy sees the exact stage sequence.

    This is the criterion's evidence. It fails if the orchestrator is decorative,
    because ``kinds`` is built only from ``step`` calls that actually happened.
    """
    run = await harness.start_run()
    spy = RecordingOrchestrator()
    harness.use_orchestrator(spy)

    # -- the first drain drives the run to the approval gate -------------
    # The refund's EXECUTE stage raises RunParked, which propagates out of the
    # worker (``drain_once`` suppresses it) — so the drain returns normally, and
    # the spy has recorded every stage up to and including that EXECUTE.
    assert await harness.drain(worker_id="w-1") is True

    sequence = list(spy.kinds)
    assert sequence, (
        "the spy recorded no stages: the pump did not call orchestrator.step. "
        "The orchestrator is being carried, not run — which is the defect this "
        "test exists to catch."
    )
    assert sequence == [
        StepKind.CLASSIFY,
        StepKind.RETRIEVE,
        StepKind.PLAN,
        StepKind.EXECUTE,
        StepKind.PLAN,
        StepKind.EXECUTE,
        StepKind.PLAN,
        StepKind.EXECUTE,
        StepKind.PLAN,
        StepKind.EXECUTE,
    ], f"the orchestrator did not drive the documented stage sequence: {sequence}"

    # The final EXECUTE is the one that parked on the refund — the stage that
    # raises ``RunParked``. A spy that recorded after delegating would have lost
    # it, so this pins the entry-recording decision.
    assert sequence[-1] is StepKind.EXECUTE
    assert await harness.status_of(run) is not None  # the row survived the park

    # -- a second spy drain shows RESPOND, on the post-approval path -------
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)

    resume_spy = RecordingOrchestrator()
    harness.use_orchestrator(resume_spy)
    assert await harness.drain(worker_id="w-2") is True

    final = await harness.reload(run)
    assert final.status.value == "completed"
    # The resume path (``_resume``) is deliberately NOT the pump, so it does not
    # dispatch through the orchestrator — and the spy says so, which is the other
    # half of "the orchestrator drives the *pump*": the paths that do not go
    # through ``_pump`` do not magically start consulting it.
    assert resume_spy.kinds == [], (
        f"the resume path dispatched through the orchestrator, but it should not: "
        f"{resume_spy.kinds}"
    )


async def test_a_raising_orchestrator_stops_the_pump_dead(
    harness: Harness,
) -> None:
    """A guard, in the opposite direction: a raising ``step`` is no longer legal.

    The four doubles the M4 work inverted (``UnusedOrchestrator``,
    ``_NullOrchestrator`` and their kin) used to raise on the claim that the pump
    never consulted them. This pins the inversion directly: if the pump consults
    the orchestrator, an orchestrator whose ``step`` raises must make the run
    fail rather than quietly complete. A test that only asserted the happy-path
    sequence could still pass if the pump called ``step`` once and then drove
    itself; this one cannot.
    """
    spy = _RaisingOrchestrator()
    run = await harness.start_run()
    harness.use_orchestrator(spy)

    with pytest.raises(NotImplementedError):
        await harness.drain(worker_id="w-1")

    # The raise aborts before any stage runs, so the run is not completed by some
    # other path that avoided the orchestrator.
    reloaded = await harness.reload(run)
    assert reloaded.status.value != "completed", (
        "the run completed even though the orchestrator it was driven through "
        "raised — the pump did not actually dispatch through it"
    )
