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
    # The bound is deliberately loose (>=150ms) because it must survive a loaded
    # CI machine while still being unreachable by a constant: the fast run's
    # recorded latency is tens of milliseconds at most, so no single hard-coded
    # value satisfies both halves of this comparison.
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
    that the *second* call (``billing.get_invoice``, a name the run knows it
    will ask for) sleeps a known 250ms inside the dispatch the gateway times.

    The wrapper is a real ``ToolGateway`` that delegates to the real
    ``MCPToolGateway`` and then rewrites only the ``latency_ms`` it reports, so
    the row still comes from the real store, the real runtime and the real MCP
    servers. If the runtime forwarded something other than
    ``ToolResult.latency_ms`` -- its own ``perf_counter`` reading, a constant,
    or nothing at all -- the slowed call could not come back as the large one.
    """
    import asyncio

    from opspilot.ports.tool_gateway import ToolResult

    inner = harness.gateway
    slowed_name = "billing.get_invoice"

    class SlowedDispatchGateway:
        """Delegates every call to the real gateway; makes one dispatch slow."""

        def __init__(self) -> None:
            self.slow_calls: list[int] = []

        async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolResult:
            result = await inner.call_tool(name, arguments)
            if name != slowed_name:
                return result
            await asyncio.sleep(0.25)
            self.slow_calls.append(result.latency_ms)
            return ToolResult(
                tool_name=result.tool_name,
                ok=result.ok,
                result=result.result,
                error=result.error,
                latency_ms=result.latency_ms + 250,
            )

        async def list_tools(self) -> object:
            return await inner.list_tools()

    gateway = SlowedDispatchGateway()
    run = await harness.start_run()
    assert await harness.drain_with(gateway) is True  # type: ignore[arg-type]
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain_with(gateway, worker_id="w-2") is True  # type: ignore[arg-type]

    assert gateway.slow_calls, f"the run never dispatched {slowed_name!r}"

    rows = {c.tool_name: c for c in harness.tool_calls(run.id)}
    assert slowed_name in rows, f"the run never persisted a {slowed_name!r} row"
    slowed = rows[slowed_name]
    assert slowed.latency_ms is not None
    assert slowed.latency_ms >= 250, (
        f"the only dispatch the gateway deliberately delayed by 250ms recorded "
        f"{slowed.latency_ms}ms on its {slowed_name!r} row; the runtime is not "
        "persisting the gateway's measured latency"
    )

    # The control: an undelayed call in the same run must NOT also read >= 250ms.
    # Without this half, a constant of 250+ would satisfy the assertion above.
    others = [
        c.latency_ms
        for name, c in rows.items()
        if name != slowed_name and c.status == "executed" and c.latency_ms is not None
    ]
    assert others, "expected other executed calls in the run to compare against"
    assert all(value < 250 for value in others), (
        f"undelayed tool calls recorded latencies {others}, all >= the 250ms the "
        "one deliberately-slowed call was given; a constant cannot distinguish "
        "them, so this assertion is what makes the one above a measurement"
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
