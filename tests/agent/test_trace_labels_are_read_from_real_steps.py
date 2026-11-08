"""The trace's ``detail`` strings must be read from keys writers actually write.

``GET /api/runs/{id}/trace`` projects each step to a human-readable ``detail``.
Those strings are assembled in ``api/routers/runs.py`` by reading keys out of
``AgentStep.output`` -- and the keys are spelled twice, in the writer and in the
reader, with nothing connecting them.

Two were wrong, and both failed *silently*, which is the only kind that lasts:

- ``state_change`` read ``output.get("to")``. Both writers
  (``persistence/repositories.py`` and ``tracing/recorder.py``) emit
  ``to_status``. The ``or`` fallback meant the timeline showed ``to executing``
  by accident, and the correct key was never actually needed.
- ``tool_call`` had a whole branch reading ``output.get("tool_name")`` for a
  step type **nothing in ``src/`` ever records**. The dashboard has a renderer
  for an event that cannot exist.

Neither raised. A timeline that renders ``to None`` on every state change is a
dashboard that looks broken in a way nobody can trace to its cause.

This test drives the real golden path, reads the ``AgentStep`` rows back from
the database, and asserts the detail for each real step type is non-empty and
names something true -- so a key can only be renamed in the writer and the
reader *together*.
"""

from __future__ import annotations

from opspilot.adapters.persistence import models
from opspilot.api.routers.runs import _detail_for, _label_for
from opspilot.domain.runs import RunStatus
from tests.agent._golden_harness import Harness

#: The step types the golden path actually records, discovered rather than
#: assumed. Asserted against below so a new step type cannot appear without
#: anyone deciding whether it renders.
_REAL_WORK_STEP_TYPES = frozenset({"classification", "retrieval", "planning", "response"})

#: The step type the *state* writers record, as opposed to the work steps the
#: runtime records. It is not a work step, but it is a step the trace renders,
#: so it is in the known set too.
_STATE_CHANGE_STEP_TYPE = "state_change"

#: Every step type the tree is allowed to record. A step type outside this set
#: is one nobody has decided how it should read in the timeline.
#:
#: This set is subtracted *from* the recorded types, not the other way round.
#: Filtering the recorded types by the known ones before comparing them makes
#: the comparison blind to a new type by construction -- and that is precisely
#: what this guard exists to catch.
_KNOWN_STEP_TYPES = _REAL_WORK_STEP_TYPES | {_STATE_CHANGE_STEP_TYPE}


async def test_every_real_step_renders_a_non_empty_label(harness: Harness) -> None:
    """No recorded step renders as an empty label."""
    steps = await _drive(harness)
    for step in steps:
        label = _label_for(step.step_type)
        assert label and label.strip(), f"seq={step.sequence} {step.step_type} rendered no label"


async def test_no_real_step_renders_as_to_none(harness: Harness) -> None:
    """The state-change detail names the status it moved to.

    This is the assertion that would have caught the ``to`` / ``to_status``
    mismatch. It reads the row the *writer* produced and requires the *reader*
    to find a real status in it, so renaming the key on either side alone turns
    this red instead of turning the timeline into ``to None``.
    """
    steps = await _drive(harness)
    changes = [s for s in steps if s.step_type == "state_change"]
    assert changes, "the golden path recorded no state changes to check"

    statuses = {status.value for status in RunStatus}
    for step in changes:
        detail = _detail_for(step.step_type, step.output)
        assert detail.startswith("to "), (
            f"seq={step.sequence}: a state change rendered {detail!r}; a step that "
            "moved to a status must say which one. A detail of '' or 'to None' "
            "here means the reader is looking for a key the writer never emits."
        )
        named = detail.removeprefix("to ")
        assert named in statuses, (
            f"seq={step.sequence}: the state change names {named!r}, which is not a "
            f"RunStatus. Known: {sorted(statuses)}"
        )


async def test_a_step_type_nothing_records_has_no_detail_branch(harness: Harness) -> None:
    """``tool_call`` is not a real step type, so it must not have a renderer.

    Kept as a test rather than a deletion so that if someone later starts
    recording ``tool_call`` steps, this fails and asks the question again --
    adding the renderer is a deliberate act, not a side effect.
    """
    steps = await _drive(harness)
    recorded = {s.step_type for s in steps}
    assert "tool_call" not in recorded, (
        "a tool_call step is now recorded; the trace's renderer for it was deleted "
        "in M7 because nothing produced one. Re-add the branch deliberately if the "
        "step type is real."
    )
    assert _detail_for("tool_call", {"tool_name": "billing.issue_refund"}) == "", (
        "a detail branch exists for a step type nothing records -- code that can only ever be wrong"
    )


async def test_the_work_step_types_are_the_ones_the_trace_renders(
    harness: Harness,
) -> None:
    """Every recorded step type renders a meaningful detail.

    The guard on the guard: if the runtime adds a step type, this fails until
    someone decides how it should read in the timeline.

    It used to fail to do that. The recorded types were **filtered** by the
    known ones before being compared, so a type the runtime had just begun
    writing was filtered out of the comparison and the guard stayed green --
    which is the one thing this guard exists to prevent, and it happened
    silently. The comparison is now over every recorded type, with the known
    set subtracted from it rather than the other way round.
    """
    steps = await _drive(harness)
    recorded = {s.step_type for s in steps}

    unknown = sorted(recorded - _KNOWN_STEP_TYPES)
    assert not unknown, (
        f"the run recorded step types nothing here knows about: {unknown}. Every "
        "step type the golden path produces must say something in the timeline: "
        "a row labelled 'Next action proposed' with no detail tells the reader "
        "the agent did something without saying what. Add the type to "
        "_KNOWN_STEP_TYPES and decide how it renders, or delete the branch that "
        "renders it."
    )

    work = recorded & _REAL_WORK_STEP_TYPES
    assert work == _REAL_WORK_STEP_TYPES, (
        f"the golden path recorded work steps {sorted(work)}, expected "
        f"{sorted(_REAL_WORK_STEP_TYPES)}"
    )
    for step_type in sorted(_KNOWN_STEP_TYPES):
        matching = [s for s in steps if s.step_type == step_type]
        assert matching, f"no {step_type} step was recorded"
        for step in matching:
            detail = _detail_for(step.step_type, step.output)
            assert detail, (
                f"a {step_type} step rendered an empty detail. Every step type the "
                "golden path produces must say something in the timeline: a row "
                "labelled 'Next action proposed' with no detail tells the reader "
                "the agent did something without saying what."
            )


async def _drive(harness: Harness) -> list[models.AgentStep]:
    """Run the golden path to completion and return its real step rows.

    Typed as the real persisted row (``models.AgentStep``) rather than a bare
    ``object``: the tests below read ``.step_type``, ``.sequence`` and ``.output``
    off each row, and an untyped sequence is what let the ``to`` / ``to_status``
    mismatch survive -- the helper hid the shape the assertions depend on.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True
    return harness.steps(run.id)
