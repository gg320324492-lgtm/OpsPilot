"""The ``model_called`` audit event: one per real model call, carrying its usage.

Written from the defect, not from ``agents/runtime.py``. ``ModelUsage``
documents itself as "written as a ``model_called`` event"
(``ports/model_provider.py``) and ``docs/data-model.md`` §2 lists the event type,
but no code wrote one. ``docs/evals.md`` §1 takes metrics 10-12 from exactly
these events, so ``evals/runner.py`` read none and honestly reported ``0``
tokens and ``$0.0000`` cost over real runs -- a measurement-shaped zero, which is
worse than an absent row because it looks like a number.

Why these tests drive the real worker over the golden harness
-------------------------------------------------------------
A test that asserted "``record_audit`` was called with ``event_type='model_called'``"
would pass against an implementation that wrote an event the eval reader cannot
parse, or that wrote one per *step* rather than per *model call*. The runner
reads the payload keys back out of SQLite (``runner._model_calls``), so these
tests read the same rows the same way: the run goes through ``drain_once`` over
real ``Sql*`` stores and the real MCP servers, and the assertions are about what
``Harness.audit_events`` returns.

The counting assertion is the one that matters
----------------------------------------------
"One event per real model call" is a claim about a *count*. The golden path makes
a known number of calls -- one classification, four planning rounds, one response
-- so the test compares the number of events against the number the run actually
made, read from the step timeline, rather than against a constant a refactor
could silently invalidate. The classification and response events must carry the
provider's tokens; the planning events must say, explicitly, that theirs are not
observable (``choose_tool`` returns a bare ``dict`` with no usage record).
"""

from __future__ import annotations

from uuid import UUID

from opspilot.adapters.models.fake import FakeModelProvider
from opspilot.agents.runtime import AUDIT_MODEL_CALLED
from opspilot.domain.runs import AgentRun
from opspilot.ports.model_provider import ModelResponse, ModelUsage, TModel
from tests.agent._golden_harness import Harness

# The step types that correspond, one-to-one, to a model call the runtime made:
# ``classification`` and ``response`` are ``generate_structured`` calls and
# ``planning`` is a ``choose_tool`` call. Every such step must have a matching
# ``model_called`` event, no more and no fewer.
_MODEL_STEP_TYPES: frozenset[str] = frozenset({"classification", "planning", "response"})


def _model_events(harness: Harness, run_id: UUID) -> list[dict[str, object]]:
    """Every ``model_called`` audit payload for a run, in write order."""
    return [
        dict(event.payload or {})
        for event in harness.audit_events(run_id)
        if event.event_type == AUDIT_MODEL_CALLED
    ]


async def _run_to_completion(harness: Harness) -> AgentRun:
    """Drive the golden path to ``COMPLETED``, approving the parked refund.

    The response call happens on the pass *after* the human approves, so the run
    has to finish before every model call -- including the response -- has been
    made and its event written.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True
    return run


# ---------------------------------------------------------------------------
# One event per model call, and the planning calls are among them.
# ---------------------------------------------------------------------------


async def test_one_model_called_event_per_model_call(harness: Harness) -> None:
    """The event count equals the number of model calls the run made.

    The count of model calls is read from the run's own step timeline -- each
    ``classification``/``planning``/``response`` step *is* a model call the
    runtime made -- and compared against the number of ``model_called`` events.
    A missing event (the original defect) or a duplicated one fails here.
    """
    run = await _run_to_completion(harness)

    model_steps = [s for s in harness.steps(run.id) if s.step_type in _MODEL_STEP_TYPES]
    events = _model_events(harness, run.id)

    assert model_steps, "the run recorded no model steps at all"
    assert len(events) == len(model_steps), (
        f"the run made {len(model_steps)} model calls "
        f"({sorted(s.step_type for s in model_steps)}) but wrote {len(events)} "
        "model_called events; every real model call must leave exactly one"
    )


async def test_planning_calls_are_recorded_as_model_called(harness: Harness) -> None:
    """Planning is a model call, and it writes an event -- with an honest null.

    ``choose_tool`` returns a bare ``dict`` by port design, so its tokens and cost
    are genuinely unobservable. The event still exists (the call happened and its
    latency was measured at the call site), and it says so: ``tokens_available``
    is ``False`` and the three token/cost fields are ``None``, not ``0``.
    """
    run = await _run_to_completion(harness)

    planning_steps = [s for s in harness.steps(run.id) if s.step_type == "planning"]
    assert planning_steps, "the golden path made no planning calls"

    events = _model_events(harness, run.id)
    planning_events = [e for e in events if e.get("tokens_available") is False]
    assert len(planning_events) == len(planning_steps), (
        f"{len(planning_steps)} planning calls but {len(planning_events)} events "
        "flagged as token-less; a planning call is a model call and must be recorded"
    )

    for event in planning_events:
        assert event["latency_ms"] is not None
        assert event["input_tokens"] is None
        assert event["output_tokens"] is None
        assert event["estimated_cost_usd"] is None


# ---------------------------------------------------------------------------
# The classification event carries the provider's reported numbers, not a constant.
# ---------------------------------------------------------------------------


async def test_classification_event_carries_the_providers_reported_usage(
    harness: Harness,
) -> None:
    """The tokens and cost on the event are the values the provider *reported*.

    ``latency_ms is not None`` (or ``>= 0``) cannot tell a real value from a
    hard-coded one. So the provider is replaced with one that declares
    unmistakable numbers, and the persisted event must carry exactly those. A
    runtime that hard-coded ``0``, or forwarded the wrong field, fails here.
    """
    declared = ModelUsage(
        provider="declared-provider",
        model="declared-model",
        latency_ms=1234,
        input_tokens=1840,
        output_tokens=312,
        estimated_cost_usd=0.0184,
    )

    class DeclaredUsageProvider(FakeModelProvider):
        """A fake that reports a specific, unmistakable usage on every call.

        The override matches the port's generic signature exactly, so the
        substitute still *is* a ``ModelProvider`` rather than an untyped wrapper
        (the same reasoning as ``test_step_latency``'s declared-latency provider).
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
            response.usage = declared
            return response

    harness.provider = DeclaredUsageProvider(scenario="duplicate_charge")
    run = await _run_to_completion(harness)

    events = _model_events(harness, run.id)
    classification = [e for e in events if e.get("provider") == declared.provider]
    assert classification, (
        "no model_called event carried the provider's own usage; the runtime "
        f"did not forward ModelUsage. Events were: {events}"
    )
    # The classification call is the first ``generate_structured`` call; its event
    # must carry every field of the declared usage, unaltered.
    first = classification[0]
    assert first["input_tokens"] == declared.input_tokens
    assert first["output_tokens"] == declared.output_tokens
    assert first["estimated_cost_usd"] == declared.estimated_cost_usd
    assert first["latency_ms"] == declared.latency_ms
    assert first["model"] == declared.model
    assert first["tokens_available"] is True


async def test_the_classification_event_is_not_a_fabricated_zero(harness: Harness) -> None:
    """The committed fake fixture reports *zero* tokens -- and the event says zero.

    This is the other half of the previous test, and it is the distinction the
    eval table turns on: a ``0`` from the provider and a ``0`` the runtime
    invented are different facts. The committed ``duplicate_charge`` fixture is a
    hand-written placeholder with ``input_tokens: 0``; with it, the event must
    carry ``0`` (``usage`` *was* available, it just happened to be zero), not
    ``None`` -- ``tokens_available`` is what tells the two apart.
    """
    run = await _run_to_completion(harness)

    events = _model_events(harness, run.id)
    # Filter on the flag, not the provider name: the planning events carry the
    # run's provider ("fake") too, and it is ``tokens_available`` that distinguishes
    # "usage was reported and happened to be zero" from "usage was not observable".
    structured = [e for e in events if e.get("tokens_available") is True]
    assert structured, "no classification/response event was recorded"

    for event in structured:
        assert event["provider"] == "fake"
        assert event["input_tokens"] == 0
        assert event["output_tokens"] == 0
        assert event["estimated_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# The event survives the transaction discipline: a run that later fails keeps it.
# ---------------------------------------------------------------------------


async def test_a_failed_run_keeps_its_model_call_accounting(harness: Harness) -> None:
    """A model call that happened is an audited fact even if the run then fails.

    ``record_audit`` commits in its own transaction, so the classification event
    is on disk before anything downstream can fail. The failure is provoked *after*
    classification by a gateway that cannot reach the MCP server, and the
    classification event must still be present -- otherwise metrics 11/12 would
    under-count exactly the runs an operator is most likely to investigate.
    """
    from tests.agent._golden_harness import UnreachableGateway

    run = await harness.start_run()
    assert await harness.drain_with(UnreachableGateway()) is True

    final = await harness.reload(run)
    assert final.status.value == "failed"

    events = _model_events(harness, run.id)
    assert events, "the failed run kept none of its model-call accounting"
    assert any(e.get("tokens_available") is True for e in events), (
        "the classification call happened before the failure and must be recorded"
    )
