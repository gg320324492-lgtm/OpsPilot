"""Every transition the runtime takes must be one the table allows.

## Why this file exists

``docs/agent-state-machine.md`` §2 is the specification: a table of the edges a
run may take. ``src/opspilot/domain/runs.py`` holds that table, and
``AgentRun.transition_to`` is its one implementation — it looks the edge up and
raises ``IllegalTransition`` when it is absent.

Two other guards already existed, and both were blind to the defect this file
is about:

* ``tests/unit/test_run_state.py`` compares the table against the **enum** — it
  proves every state has a row, not that the runtime stays on the edges.
* ``tests/unit/test_state_machine_document_matches_the_code.py`` compares the
  **document** against the **table** — it proves the prose and the data agree.

Neither of them asks whether the *code* walks the edges the table lists. And it
did not: the golden path takes ``EXECUTING → PLANNING`` once per tool round —
four times on the recorded run — and the table did not carry that edge. It
offered ``EXECUTING → EXECUTING`` instead, which the runtime never takes. So
the table described a machine the code did not run, and the two guards above
were green throughout, because both of them compare two artefacts that agreed
with each other and neither with the runtime.

Nothing noticed because **no runtime transition was ever checked**. The
runtime assigned ``ctx.run.status`` directly, so a transition the table
forbids was written silently. The table now carries the edge the code takes,
and the runtime's progression goes through ``transition_to``, which refuses an
edge the table does not have — but the check only helps if something asserts
the whole sequence.

## What this file asserts

That is the whole of it: drive a **real** run — the golden path, through the
real worker, the real stores and the real MCP gateway — read the transitions
back from the ``state_change`` rows the run actually wrote, and assert that
every one of them is an edge ``ALLOWED_TRANSITIONS`` allows.

Reading the recorded rows rather than the returned status matters: the
recording is written by the store in the same transaction as the status
update, so a run that reached the right end state by an illegal route is still
caught here.

What it deliberately does **not** assert:

* that the run reached ``COMPLETED`` — ``test_golden_path.py`` owns the
  outcome; this file is about the route, not the destination;
* that the sequence is a *particular* sequence — it is free to change; only
  the edges are constrained;
* that every edge in the table is taken — a table may carry an edge the
  runtime does not use. It may not carry one the runtime uses and the table
  does not.
"""

from __future__ import annotations

from opspilot.domain.runs import ALLOWED_TRANSITIONS, RunStatus

from ._golden_harness import Harness


async def test_every_transition_the_golden_path_takes_is_in_the_table(
    harness: Harness,
) -> None:
    """The recorded transitions are all edges the table allows.

    The run is driven to completion through the real worker, so the sequence
    includes every edge the runtime takes on a full run: the chain up to
    ``EXECUTING``, the re-plan between tool rounds, the park and the resume,
    and the final answer.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    changes = harness.state_changes(run.id)
    assert changes, "the run recorded no transitions at all"

    illegal = [(frm, to) for frm, to in changes if to not in ALLOWED_TRANSITIONS[RunStatus(frm)]]
    assert not illegal, (
        f"the runtime took transitions the table forbids: {illegal}. "
        "The table is the specification (docs/agent-state-machine.md §2); a "
        "transition it does not carry is a status the run was never allowed "
        "to be in."
    )


async def test_the_re_plan_edge_is_actually_taken(harness: Harness) -> None:
    """``EXECUTING → PLANNING`` is on the table *because* the runtime takes it.

    The other test would pass with an empty table of legal edges if the runtime
    took none — it only says every taken edge is legal. This one says the edge
    the table now carries is a real one, taken on every tool round, so the edge
    cannot quietly become another unused edge of the kind the table used to
    offer (``EXECUTING → EXECUTING``).

    The count is "more than once" rather than an exact number on purpose: how
    many tool rounds the golden path takes is the harness's business, and a
    change to it should not fail a test about the state machine. What must not
    change is that the run goes back to ``PLANNING`` between tool calls.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    changes = harness.state_changes(run.id)
    replans = [pair for pair in changes if pair == ("executing", "planning")]

    assert len(replans) > 1, (
        "the run took EXECUTING -> PLANNING "
        f"{len(replans)} time(s); the golden path calls several tools and each "
        "is a separate plan->gate->execute round, so the edge is taken once "
        "per round. A count of one would mean the table carries an edge the "
        "runtime barely uses -- which is what EXECUTING -> EXECUTING was."
    )
