"""Every persisted ``agent_steps`` row carries a measured latency.

Written from the acceptance criterion, not from ``agents/runtime.py``.
``docs/milestones.md`` §M6 says: "The trace shows every step with latency."
Before M7a exactly **one** of the golden path's twenty-one steps carried one --
the ``classification`` step -- while ``retrieval``, ``planning`` and ``response``
passed nothing at all to ``TraceRecorder.record_step``, even though the elapsed
time had already been measured by the retrieval stack, the model providers and
the MCP gateway.

Why this file asserts against the database and not a recorder double
--------------------------------------------------------------------
A test that said "``record_step`` was called with ``latency_ms=42``" would pass
against an implementation that measured the wrong thing and passed the right
number, and would keep passing if the runtime started recording steps that were
never persisted -- the defect the M5e F1 finding is about (``FakeRunStore``
answered every probe, so the dashboard's three panels were empty in production
for four milestones). These tests therefore drive the **real worker** through
``drain_once``, over real ``Sql*`` stores and the real in-process MCP servers,
and read ``agent_steps`` back out of SQLite. That is the same posture as
``test_golden_path.py``: the assertion is about what a deployment would show.

The step types asserted here are the ones the runtime *emits*. ``state_change``
rows are deliberately excluded, and the reason is a specification question, not
a convenience -- see :data:`_TIMED_STEP_TYPES` and the module docstring of
``tests/agent/test_step_latency.py`` for the full argument.
"""

from __future__ import annotations

from opspilot.agents.runtime import executed_lookup_for
from opspilot.domain.tools import ToolCallStatus
from opspilot.tracing.recorder import TraceRecorder
from tests.agent._golden_harness import Harness

# The step types the runtime emits that stand for a unit of work with a real
# duration: a model call, a retrieval, or an executed tool call. ``state_change``
# is excluded -- see below.
_TIMED_STEP_TYPES: frozenset[str] = frozenset(
    {"classification", "retrieval", "planning", "response", "tool_call"}
)

# ``state_change`` rows are written by ``SqlRunStore.set_status`` in the same
# transaction as the status update (``adapters/persistence/repositories.py``
# ``set_status``). There is no external work between the run's old and new
# status -- no model call, no tool call, no retrieval -- so there is no duration
# to measure. Recording a latency for one would mean timing a database UPDATE,
# which reports the database's speed, not the agent's. They are listed here so
# that the exclusion is a stated decision with a check on it (``test_every_state
# _change_row_is_documented_as_untimed``) rather than a silent gap.
_UNTIMED_STEP_TYPES: frozenset[str] = frozenset({"state_change"})


async def test_every_work_step_persists_a_measured_latency(harness: Harness) -> None:
    """Each step that stands for real work carries a non-null ``latency_ms``.

    Acceptance criterion (``docs/milestones.md`` §M6): "The trace shows every
    step with latency."

    Read back from ``agent_steps`` after a full golden-path run, so a step that
    was never written -- or written with ``latency_ms = NULL`` -- fails here
    rather than passing because the runtime *intended* to record one.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    steps = harness.steps(run.id)
    assert steps, "the run recorded no steps at all"

    timed = [s for s in steps if s.step_type in _TIMED_STEP_TYPES]
    missing = [s for s in timed if s.latency_ms is None]
    assert not missing, (
        f"{len(missing)} of {len(timed)} work steps persisted a NULL latency_ms "
        f"-- the trace would render them as '--': "
        + ", ".join(f"seq={s.sequence} {s.step_type}" for s in missing)
    )

    # A measured latency is a real elapsed time, not a placeholder. The column is
    # nullable, so a value of exactly 0 is either a genuine sub-millisecond call
    # or a hard-coded zero; requiring ``>= 0`` would accept a constant. The
    # golden path's four MCP calls and its model calls are all in-process and
    # finish inside a millisecond, so 0 is a legitimate measurement here and is
    # not, on its own, evidence of a defect. What is asserted is that the column
    # is *populated* -- the distinction that matters is NULL vs not-NULL, and that
    # is what the previous implementation got wrong.
    for step in timed:
        assert step.latency_ms is not None
        assert isinstance(step.latency_ms, int)
        assert step.latency_ms >= 0


async def test_retrieval_and_response_steps_are_timed(harness: Harness) -> None:
    """The two step types whose measured latency was available but discarded.

    ``retrieval`` times the ``await retrieval(...)`` call and ``response``
    forwards ``ModelResponse.usage.latency_ms`` from ``generate_structured``.
    Both already had a measurement in hand at the ``record_step`` call site --
    the defect was that neither passed it -- so these are the two a mutation
    will turn red on its own.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    types = {s.step_type for s in harness.steps(run.id)}
    assert "retrieval" in types, f"the run performed no retrieval; steps were {sorted(types)}"
    assert "response" in types, f"the run composed no response; steps were {sorted(types)}"

    for wanted in ("retrieval", "response"):
        rows = [s for s in harness.steps(run.id) if s.step_type == wanted]
        assert rows, f"no {wanted!r} step was recorded"
        for row in rows:
            assert row.latency_ms is not None, (
                f"the {wanted!r} step persisted a NULL latency; the measured "
                "elapsed time was discarded at the record_step call site"
            )


async def test_a_retrieval_latency_reflects_how_long_retrieval_actually_took(
    harness: Harness,
) -> None:
    """The persisted retrieval latency is a *measurement*, not a constant.

    This is the assertion that ``test_every_work_step_persists_a_measured_latency``
    could not make. Asserting ``latency_ms is not None`` is satisfied equally by
    ``latency_ms=0``, by a hard-coded ``42``, and by the real elapsed time -- and
    the first version of this file proved it: with ``latency_ms=42`` substituted
    for the measured value, every test in it stayed green.

    So the value is checked against an independent measurement of the same work.
    Two retrievals are driven through the runtime, one of them made to take
    measurably longer than the other by the retriever itself; if the recorded
    number is a real ``perf_counter`` measurement it will track the difference,
    and a constant cannot. The slow retriever sleeps, so the gap is orders of
    magnitude larger than the timing noise -- the assertion is not racing the
    clock.

    The harness's own retrieval callable is replaced for one run rather than
    mocked, so the step still comes from ``runtime._retrieve`` and the row is
    still persisted by the real recorder.
    """
    import asyncio
    import dataclasses

    from opspilot.adapters.models.fake import FakeModelProvider

    original = harness.stack.retrieval

    async def slow_retrieval(query: str) -> list[object]:
        await original(query)
        # ``asyncio.sleep`` rather than ``time.sleep``: blocking the loop would
        # be the wrong kind of slow (ASYNC240/ASYNC251), and what is under test is
        # the wall-clock duration the runtime measures, which an awaited sleep
        # reproduces exactly.
        await asyncio.sleep(0.25)
        return []

    # ``RetrievalStack`` is a frozen dataclass, so the stack is replaced with a
    # copy carrying the slowed callable rather than mutated -- the runtime reads
    # ``stack.retrieval`` through ``drain_once`` either way, and a frozen fixture
    # is the safer thing to leave behind.
    slow_stack = dataclasses.replace(harness.stack, retrieval=slow_retrieval)  # type: ignore[arg-type]
    harness.stack = slow_stack
    slow_run = await harness.start_run()
    assert await harness.drain(worker_id="w-slow") is True

    harness.stack = dataclasses.replace(harness.stack, retrieval=original)
    # A fresh provider for the second run: ``FakeModelProvider`` advances a
    # per-method cursor, so replaying the same instance would answer the second
    # run with the first run's exhausted script (documented in
    # ``_golden_harness.Harness.__init__``).
    harness.provider = FakeModelProvider(scenario="duplicate_charge")
    fast_run = await harness.start_run()
    assert await harness.drain(worker_id="w-fast") is True

    def _latency(run_id: object) -> int | None:
        rows = [s for s in harness.steps(run_id) if s.step_type == "retrieval"]  # type: ignore[arg-type]
        assert rows, "no retrieval step was recorded"
        return rows[0].latency_ms

    slow_ms = _latency(slow_run.id)
    fast_ms = _latency(fast_run.id)
    assert slow_ms is not None and fast_ms is not None

    # The slow retrieval slept 250ms; the recorded number must reflect that.
    # This floor is safe under load and is deliberately not the 250 the sleep
    # asked for: it is a *one-sided lower* bound on a value the test itself made
    # large, so a busier machine only pushes the measurement further above it and
    # can never fail it. The 100ms of slack absorbs the accounting between the
    # sleeper and the timed call site. The bound is what makes a constant
    # unreachable, and the delta below is the assertion that actually tracks the
    # sleep: both runs are measured under the same load, so their difference --
    # unlike either absolute number -- does not move when the host is busy.
    assert slow_ms >= 150, (
        f"a retrieval that slept 250ms recorded {slow_ms}ms; the persisted "
        "latency is not the elapsed time the retriever actually spent"
    )
    assert slow_ms - fast_ms >= 150, (
        f"the deliberately-slowed retrieval recorded {slow_ms}ms and the normal "
        f"one {fast_ms}ms; a constant latency_ms would produce equal numbers, "
        "so this test cannot be satisfied by a hard-coded value"
    )


async def test_a_response_latency_tracks_the_provider_reported_usage(
    harness: Harness,
) -> None:
    """The ``response`` step forwards the provider's own reported latency.

    ``response.usage.latency_ms`` is produced by the provider, not by the
    runtime -- the real adapters time the HTTP call, and ``FakeModelProvider``
    replays whatever the fixture recorded. So the provider is replaced with one
    that *declares* a specific latency, and the persisted step must carry exactly
    that number.

    This distinguishes "the runtime forwarded the measurement it was handed"
    from "the runtime invented a number", which a non-null assertion cannot: any
    non-null value passes the weaker test, but only the declared value passes
    this one.
    """
    from opspilot.adapters.models.fake import FakeModelProvider
    from opspilot.ports.model_provider import ModelResponse, ModelUsage, TModel

    declared_ms = 4321

    class DeclaredLatencyProvider(FakeModelProvider):
        """A fake that reports a specific, unmistakable latency.

        The override matches the port's generic signature exactly -- including
        ``schema: type[TModel]`` and ``ModelResponse[TModel]`` -- rather than
        taking ``**kwargs: object``. A looser signature still runs, but it
        silently stops *being* a ``ModelProvider``: the wrapper would accept any
        call shape and return an untyped value, which is precisely the drift
        this suite exists to catch. Substituting only the ``usage`` field keeps
        the recorded structured value untouched, which is what the test needs.
        """

        async def generate_structured(
            self,
            *,
            system: str,
            prompt: str,
            schema: type[TModel],
            timeout_seconds: float | None = None,
        ) -> ModelResponse[TModel]:
            response = await super().generate_structured(
                system=system,
                prompt=prompt,
                schema=schema,
                timeout_seconds=timeout_seconds,
            )
            response.usage = ModelUsage(
                provider="test",
                model="test",
                latency_ms=declared_ms,
                input_tokens=1,
                output_tokens=1,
                estimated_cost_usd=0.0,
            )
            return response

    harness.provider = DeclaredLatencyProvider(scenario="duplicate_charge")
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-declared") is True
    # The first drain parks on the refund's approval gate; the response is
    # composed on the pass after a human approves, so the run has to finish
    # before there is a ``response`` step to read.
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-declared-2") is True

    rows = [s for s in harness.steps(run.id) if s.step_type == "response"]
    assert rows, "no response step was recorded"
    assert rows[0].latency_ms == declared_ms, (
        f"the response step recorded {rows[0].latency_ms}ms but the provider "
        f"reported {declared_ms}ms; the runtime must forward the measurement it "
        "was handed rather than measuring or inventing one itself"
    )


async def test_the_golden_path_steps_are_the_types_this_file_asserts(
    harness: Harness,
) -> None:
    """The exclusion list cannot quietly swallow a step type.

    A guard written as ``assert not missing`` is only as good as its input list.
    If a future change made the runtime emit a new work step type that is not in
    ``_TIMED_STEP_TYPES``, this test would not notice -- the new type would simply
    be ignored. This one reads what the golden path actually emits and asserts
    the partition is complete: every step type is either timed or explicitly
    listed as untimed, and nothing is unaccounted for.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    emitted = {s.step_type for s in harness.steps(run.id)}
    unaccounted = emitted - _TIMED_STEP_TYPES - _UNTIMED_STEP_TYPES
    assert not unaccounted, (
        f"the golden path emits step type(s) {sorted(unaccounted)} that this "
        "file neither times nor documents as untimed; add them to "
        "_TIMED_STEP_TYPES or _UNTIMED_STEP_TYPES"
    )


async def test_the_audit_trail_latency_survives_unchanged(harness: Harness) -> None:
    """The audit payload's measured latency is untouched by this work.

    ``_gate_and_execute`` already wrote ``result.latency_ms`` into the
    ``tool_executed`` audit event. That path is *not* what §M6's "every step with
    latency" means -- an audit event is not an ``agent_steps`` row and the
    dashboard's latency column reads ``agent_steps.latency_ms``
    (``api/routers/runs.py`` ``_to_trace_step``). Asserted here so that a
    change to the step-recording path cannot quietly drop the audit value on the
    way through, which would trade one empty column for another.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    executed = [
        e
        for e in harness.audit_events(run.id)
        if e.event_type == "tool_executed" and (e.payload or {}).get("ok") is True
    ]
    assert len(executed) == 4, f"expected four audited tool executions, got {len(executed)}"
    for event in executed:
        assert isinstance((event.payload or {}).get("latency_ms"), int)


async def test_a_planning_latency_reflects_how_long_the_model_call_took(
    harness: Harness,
) -> None:
    """The ``planning`` latency is a wall-clock measurement at the call site.

    ``ModelProvider.choose_tool`` returns a bare ``dict`` -- it carries no usage
    record, so there is nothing for the provider to report (unlike
    ``generate_structured``, which returns ``ModelResponse`` and whose
    ``usage.latency_ms`` the ``response`` step forwards). The runtime therefore
    times the awaited call itself with ``perf_counter``.

    So the measurement is checked against an independent one: a subclass whose
    ``choose_tool`` sleeps for a known 250ms before delegating. If the runtime
    forwards a real ``perf_counter`` reading the persisted number tracks the
    sleep; a constant -- including the provider-reported usage latency the
    response step uses, and including a hard-coded value -- cannot exceed 150ms.

    The delay is inside the provider, not in a wrapper around the runtime, so
    the number measured is the one the ``planning`` step will actually record.
    """
    import asyncio

    from opspilot.adapters.models.fake import FakeModelProvider

    class SlowPlanProvider(FakeModelProvider):
        """A fake whose ``choose_tool`` takes a known, deliberate amount of time."""

        async def choose_tool(self, **kwargs: object) -> dict[str, object]:
            await asyncio.sleep(0.25)
            return await super().choose_tool(**kwargs)  # type: ignore[arg-type]

    harness.provider = SlowPlanProvider(scenario="duplicate_charge")
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-slow-plan") is True

    rows = [s for s in harness.steps(run.id) if s.step_type == "planning"]
    assert rows, "the run recorded no planning step"
    for row in rows:
        assert row.latency_ms is not None, (
            "the planning step persisted a NULL latency; choose_tool returns no "
            "usage record, so the runtime has to measure the call itself"
        )
        assert row.latency_ms >= 150, (
            f"a planning call that awaited 250ms of provider latency recorded "
            f"{row.latency_ms}ms; the persisted value is not the elapsed time "
            "the call site measured"
        )


async def test_every_executed_tool_call_persists_the_measured_latency(harness: Harness) -> None:
    """Every ``tool_calls`` row that executed carries the gateway's measurement.

    ``MCPToolGateway`` times its dispatch and returns the number as
    ``ToolResult.latency_ms``. The runtime wrote it to the ``tool_executed``
    audit event and dropped it on the floor, so the ``tool_calls.latency_ms``
    column -- which the schema has, ``ToolCallRow`` reads and
    ``ToolCallDetail`` returns -- stayed NULL for every call in the golden path.

    Read back from ``tool_calls`` after a real run, so a row that was never
    updated fails here.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    calls = harness.tool_calls(run.id)
    assert calls, "the run recorded no tool calls at all"
    missing = [c for c in calls if c.status == "executed" and c.latency_ms is None]
    assert not missing, (
        f"{len(missing)} of {len(calls)} tool calls executed without a persisted "
        "latency_ms; the gateway measured each dispatch and the value was "
        "dropped: " + ", ".join(f"{c.tool_name} status={c.status}" for c in missing)
    )


async def test_a_tool_call_latency_is_the_measurement_not_a_constant(harness: Harness) -> None:
    """The persisted tool-call latency tracks the gateway's own measured duration.

    ``latency_ms is not None`` cannot tell a real measurement from a hard-coded
    ``42`` -- the mistake this file was written to prevent. So the measurement
    is forced apart: the run executes four calls, and the gateway is wrapped so
    that one of them (``billing.get_invoice``, a name the run knows it will ask
    for) sleeps a known, deliberate amount inside the dispatch the gateway
    times, while reporting a ``latency_ms`` that is its own inner measurement
    plus that amount.

    The wrapper is a real ``ToolGateway`` that delegates to the real
    ``MCPToolGateway`` and then rewrites the ``latency_ms`` it reports, so the
    row still comes from the real store, the real runtime and the real MCP
    servers. If the runtime forwarded something other than
    ``ToolResult.latency_ms`` -- a constant, or nothing at all -- the persisted
    row could not carry the wrapper's number.

    Why the two assertions are equalities against the wrapper's own numbers
    ----------------------------------------------------------------------
    An earlier version of this test asserted ``slowed.latency_ms >= 250`` and
    ``all(other < 250 for other in others)``: one real dispatch compared to a
    fixed millisecond bound, and a control that bounded *every other* real
    dispatch by that same fixed number. The control's intent was right -- a
    hard-coded constant would satisfy an unbounded "the slowed call is large" --
    but the bound was wall-clock, and on a loaded machine any honest in-process
    dispatch (GC, scheduling, the SQLite write behind ``set_status``) can cross
    250ms. That is a failure mode unrelated to what the test exists to prove, and
    it is exactly the flake that was observed.

    The replacement removes the fixed bound entirely, and it is the same shape as
    the retrieval test above (``test_a_retrieval_latency_reflects_how_long_
    retrieval_actually_took``): that one compares the slowed retrieval to a fast
    one in the same test, so both move with the machine's load and the difference
    between them does not. Here the wrapper already knows the inner measurement
    it built the reported number from, so the "fast run" is in hand without a
    second run: the persisted row must equal the number its own dispatch
    reported. That is the delta taken all the way to an identity -- a pure
    arithmetic check over numbers the test controls, immune to how long any
    dispatch actually took and to how busy the machine is. A constant cannot
    satisfy it for four different reported values, and the control is no longer a
    second live measurement racing a bound.

    The deliberate delay is widened from 250ms to 1000ms so the slowed row is
    unmistakably the largest in the run. The delay now serves only that
    readable "the slowed call is the big one" property, not the equality -- the
    equality holds whatever the delay is. The cost is a fixed 0.75s on a suite
    that already sleeps 250ms twice in this file; paid once per run, it is worth
    a control that cannot flake.
    """
    import asyncio

    from opspilot.ports.tool_gateway import ToolResult

    inner = harness.gateway
    slowed_name = "billing.get_invoice"
    injected_ms = 1000

    class SlowedDispatchGateway:
        """Delegates every call to the real gateway; delays and inflates one.

        ``reported`` maps each dispatched tool name to the exact ``latency_ms``
        this wrapper handed the runtime -- for the delayed call, its own inner
        measurement plus ``injected_ms``; for every other call, the real
        gateway's measurement, unchanged. The test compares the persisted rows
        against these numbers, so it never has to bound a measurement.
        """

        def __init__(self) -> None:
            self.reported: dict[str, int] = {}

        async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolResult:
            result = await inner.call_tool(name, arguments)
            if name != slowed_name:
                self.reported[name] = result.latency_ms
                return result
            await asyncio.sleep(injected_ms / 1000)
            reported_ms = result.latency_ms + injected_ms
            self.reported[name] = reported_ms
            return ToolResult(
                tool_name=result.tool_name,
                ok=result.ok,
                result=result.result,
                error=result.error,
                latency_ms=reported_ms,
            )

        async def list_tools(self) -> object:
            return await inner.list_tools()

    gateway = SlowedDispatchGateway()
    run = await harness.start_run()
    assert await harness.drain_with(gateway) is True  # type: ignore[arg-type]
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain_with(gateway, worker_id="w-2") is True  # type: ignore[arg-type]

    assert slowed_name in gateway.reported, f"the run never dispatched {slowed_name!r}"

    rows = {c.tool_name: c for c in harness.tool_calls(run.id)}
    assert slowed_name in rows, f"the run never persisted a {slowed_name!r} row"
    slowed = rows[slowed_name]
    assert slowed.latency_ms is not None

    # The delayed call's row must be the number the wrapper reported, which is
    # its own inner measurement plus the deliberate delay. The wrapper measured
    # the inner dispatch itself; the runtime must persist *that* number plus the
    # injected delay, not a number it chose. An equality, so machine load cannot
    # move it: however long the inner dispatch took, both sides move together.
    assert slowed.latency_ms == gateway.reported[slowed_name], (
        f"the {slowed_name!r} row recorded {slowed.latency_ms}ms but the gateway "
        f"reported {gateway.reported[slowed_name]}ms to the runtime; the runtime "
        "is not persisting the latency the gateway measured"
    )

    # The control, and it no longer bounds anything: every executed row must
    # carry exactly the latency its own dispatch reported. One call cannot be
    # told from a constant in isolation, but four rows that each equal four
    # different gateway-reported numbers cannot all be a constant -- and unlike
    # the previous `all(value < 250)`, this compares each measurement to the
    # number that produced it rather than to a wall-clock bound.
    executed = {
        name: row
        for name, row in rows.items()
        if row.status == "executed" and row.latency_ms is not None
    }
    assert len(executed) >= 2, (
        f"expected several executed calls to compare against; got {sorted(executed)}"
    )
    for name, row in executed.items():
        assert name in gateway.reported, f"no dispatch was recorded for {name!r}"
        assert row.latency_ms == gateway.reported[name], (
            f"the {name!r} row recorded {row.latency_ms}ms but its dispatch "
            f"reported {gateway.reported[name]}ms; the persisted latency is not "
            "the value the gateway measured for that call"
        )

    # A readability guard on the same fact the equalities already prove: the
    # deliberately-delayed call is the largest in the run. No fixed bound -- it
    # is compared only to the other measured rows, and the widened delay makes
    # this margin generous enough that a loaded machine cannot close it.
    others: list[int] = []
    for name, row in executed.items():
        if name == slowed_name:
            continue
        assert row.latency_ms is not None
        others.append(row.latency_ms)
    assert others, "expected other executed calls in the run to compare against"
    assert slowed.latency_ms is not None and slowed.latency_ms > max(others), (
        f"the delayed call recorded {slowed.latency_ms}ms, not larger than the "
        f"undelayed calls' {others}; the recorded latency does not track the "
        "delay the gateway applied"
    )


async def test_an_idempotent_replay_keeps_the_prior_latency_and_says_so(harness: Harness) -> None:
    """A replayed call does not overwrite a real measurement with a stale one.

    When gate 4 short-circuits an ``idempotent_replay``, no dispatch happened on
    this pass, so there is no duration to record. The duplicate proposal is
    written as a *new* ``rejected`` row, and that row must carry no latency:
    it never dispatched. The rule this pins down is the negative space around
    the positive one -- the tempting-but-wrong values are ``0`` (asserting an
    instantaneous call, indistinguishable from a real sub-millisecond dispatch)
    and a copy of the original's measurement (attributing another row's
    dispatch to a row that made none).

    The original ``executed`` row's real measurement must survive the replay
    untouched.

    The replay is produced the way production produces it -- by executing the
    same idempotent refund a second time through the runtime's own gates and
    real idempotency lookup -- and asserted by reading ``tool_calls`` back out
    of the database.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    original = [c for c in harness.tool_calls(run.id) if c.tool_name == "billing.issue_refund"]
    assert len(original) == 1, f"expected one refund row, got {len(original)}"
    assert original[0].status == "executed"
    assert original[0].latency_ms is not None, (
        "the refund's own row recorded no latency; without it there is nothing "
        "for the replay branch to preserve or discard"
    )

    # Drive a second, independent run that replays the *same* refund key. The
    # runtime's real idempotency lookup is over the same run id, so the replay
    # has to be provoked inside the run that already executed the refund --
    # ``run_step`` is the same entry point ``run_loop`` uses.
    from opspilot.agents.runtime import run_step
    from opspilot.agents.schemas import ProposedAction
    from opspilot.agents.state import RunContext

    refreshed_run = await harness.runs.get(run.id)
    assert refreshed_run is not None, (
        "the just-completed run could not be re-read; RunContext needs the "
        "persisted row, not the pre-drain object"
    )
    ctx = RunContext(
        run=refreshed_run,
        ticket_subject="We were charged twice for invoice INV-2026-384.",
        ticket_body="Please investigate and fix it.",
        customer_email="billing@acme.example",
    )
    assert ctx.run is not None
    before = len(ctx.executed_tool_calls)
    replayed_ctx = await run_step(
        ctx,
        ProposedAction(
            tool_name="billing.issue_refund",
            arguments={
                "transaction_id": "TX-88219",
                "amount": 129.0,
                "reason": "duplicate charge",
            },
            reason="replay",
        ),
        gateway=harness.gateway,
        run_store=harness.runs,
        tool_call_store=harness.calls,
        approval_store=harness.approvals,
        recorder=TraceRecorder(run_id=run.id, session_factory=harness.factory),
        executed_lookup=executed_lookup_for(harness.calls),
    )
    # ``run_step`` returns the context; the record it produced is the one it
    # appended, and it is read from there rather than reconstructed.
    replayed = replayed_ctx.executed_tool_calls[before]

    rows = [c for c in harness.tool_calls(run.id) if c.tool_name == "billing.issue_refund"]
    assert len(rows) == 2, f"the replay recorded no new row; rows were {len(rows)}"
    replay_row = next(r for r in rows if r.id != original[0].id)

    assert replayed.status == ToolCallStatus.REJECTED.value, (
        f"the replay row came back {replayed.status!r}; the first branch of the "
        "idempotent_replay path records the duplicate as rejected because a "
        "second executed row would collide with the partial unique index"
    )
    assert replay_row.status == ToolCallStatus.REJECTED.value
    assert replay_row.latency_ms is None, (
        f"the duplicate row recorded latency_ms={replay_row.latency_ms}; it never "
        "dispatched, so it has no latency of its own and must not inherit the "
        f"original call's {original[0].latency_ms}ms"
    )

    # And the original measurement survives the replay untouched.
    reread = next(c for c in harness.tool_calls(run.id) if c.id == original[0].id)
    assert reread.latency_ms == original[0].latency_ms, (
        f"the replay overwrote the original refund's latency "
        f"({original[0].latency_ms} -> {reread.latency_ms}); a replay must never "
        "rewrite a real measurement"
    )
