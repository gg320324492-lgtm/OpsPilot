"""A run that spends its budget must not take the worker with it.

## Why this file exists

``MaxStepsExceeded`` used to kill the worker. The path, all in the tree at the
time: ``agents/runtime.py`` calls ``_fail_run(reason="max_steps_exceeded")``
and then ``raise MaxStepsExceeded(...)``; ``worker/loop.py::drain_once``
suppressed ``RunParked`` and ``MCPUnavailable`` and caught the provider SDK's
failures, and ``MaxStepsExceeded`` was in none of those; ``poll_forever`` is
``while True: await drain_once(...)`` with no handler. So the exception left the
loop and the process ended.

The run was fine -- ``_fail_run`` had already written
``FAILED(max_steps_exceeded)`` before the raise. What died was the worker, and
with it every run queued behind that one, until something restarted the process.

It was invisible in the deployment the dashboard uses because
``docker-compose.yml`` gives the worker ``restart: unless-stopped``: the
container came back and resumed polling. That hid the defect while charging a
full worker restart per over-budget run, and it made a browser test look flaky
-- ``web/e2e/failure-states.spec.ts`` drives a run to ``max_steps_exceeded`` on
purpose, and the worker dying mid-suite was the product, not the test.

``MCPUnavailable`` was already suppressed for exactly this reason, and its
comment says so: the runtime marks the run before raising, so the exception
carries no work left to do. ``MaxStepsExceeded`` is the same shape.

## What is asserted

1. A run driven to its budget ends ``FAILED(max_steps_exceeded)`` -- the
   outcome is unchanged, because that behaviour was already correct and pinned.
2. **The worker survives it.** ``drain_once`` returns ``True`` rather than
   raising, so ``poll_forever``'s loop reaches its next iteration. That is the
   regression: without it the next refactor reintroduces the death.
3. The next run in the queue is still claimed afterwards -- which is the
   property an operator actually feels, and the one a "returns True" assertion
   alone does not prove.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import opspilot.worker
from opspilot.adapters.models.fake import FakeModelProvider
from opspilot.agents.state import default_max_steps
from opspilot.domain.runs import RunStatus
from opspilot.settings import get_settings
from tests.agent._golden_harness import Harness

#: Small enough that the budget is spent on the first plan/execute round: one
#: step is claimed, ``run_step`` increments it, and the check at the top of the
#: next iteration sees the budget gone. Large enough that the run has actually
#: done something first, so the assertion is about a run that worked and then
#: ran out rather than one that never started.
_TINY_BUDGET = 2

#: ``opspilot.worker``'s directory, for the source assertions below.
_WORKER_DIR = Path(opspilot.worker.__file__).parent


@pytest.fixture
def tiny_budget(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Set ``MAX_STEPS`` so the run exhausts it, and restore the real default.

    The teardown undoes the environment first and *then* clears the cache, in
    that order: clearing before the undo re-reads the test's own value into the
    cache, and the next test in the process would build every ``RunContext``
    with a budget of two.
    """
    monkeypatch.setenv("MAX_STEPS", str(_TINY_BUDGET))
    get_settings.cache_clear()
    try:
        yield _TINY_BUDGET
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        assert default_max_steps() > _TINY_BUDGET, (
            "the settings cache was left holding the test's MAX_STEPS; the next "
            "test in this process would build every RunContext with it"
        )


async def test_a_run_that_spends_its_budget_does_not_kill_the_worker(
    harness: Harness,
    tiny_budget: int,
) -> None:
    """``drain_once`` returns after an over-budget run instead of raising.

    Before the fix this raised ``MaxStepsExceeded`` out of ``drain_once`` and
    therefore out of ``poll_forever``.
    """
    run = await harness.start_run()

    # If this raises, the defect is back and the test fails here rather than at
    # a later assertion.
    processed = await harness.drain()

    assert processed is True, (
        f"drain_once reported it processed nothing for run {run.id}; the budget "
        "was spent before the claim, which is a different defect"
    )

    refreshed = await harness.runs.get(run.id)
    assert refreshed is not None
    assert refreshed.status is RunStatus.FAILED, (
        f"the run ended {refreshed.status.value!r}, not FAILED -- the budget was "
        "not actually exhausted, so this test would pass without the fix"
    )
    assert refreshed.failure_reason == "max_steps_exceeded", (
        f"the run failed for {refreshed.failure_reason!r}, not max_steps_exceeded"
    )


async def test_the_worker_still_claims_the_run_queued_behind_it(
    harness: Harness,
    tiny_budget: int,
) -> None:
    """The queue keeps moving: a second run is claimed after the first dies.

    This is the half an operator feels. ``drain_once`` returning ``True`` says
    the worker survived; claiming the next run says the loop actually continued,
    which is what ``poll_forever`` does next.
    """
    first = await harness.start_run()
    assert await harness.drain() is True

    failed = await harness.runs.get(first.id)
    assert failed is not None and failed.status is RunStatus.FAILED

    # A second ticket, submitted after the first run already failed, so the
    # second drain has something to claim that was not merely left over.
    second = await harness.start_run()
    assert second.id != first.id

    # The fake advances a per-method cursor, so a spent instance answers with
    # the wrong part of the script. A fresh one is what a worker restart would
    # have given -- and is why this drains with a new provider.
    second_processed = await harness.drain_with(
        harness.gateway, provider=FakeModelProvider(scenario="duplicate_charge")
    )

    assert second_processed is True, (
        "the worker did not claim the run queued behind the over-budget one; the "
        "poll loop stopped even though drain_once returned"
    )

    # Whatever became of the second run, it was *driven*: its status moved off
    # RECEIVED, which is the observable difference between claimed and not.
    driven = await harness.runs.get(second.id)
    assert driven is not None
    assert driven.status is not RunStatus.RECEIVED, (
        "the second run is still RECEIVED; the worker claimed it and did nothing, "
        "or never claimed it at all"
    )


async def test_the_suppression_is_scoped_to_the_budget_not_a_blanket_catch(
    harness: Harness,
) -> None:
    """A programming error still escapes -- the suppression did not widen.

    ``drain_once``'s docstring records why: a worker running with broken
    in-memory state is worse than one that stopped. Widening the fix to
    ``except Exception`` would trade that property for a quieter log, so the
    boundary is pinned rather than assumed.
    """
    source = (_WORKER_DIR / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="loop.py")
    suppressions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and "suppress" in ast.dump(node)
        and "MaxStepsExceeded" in ast.dump(node)
    ]
    assert suppressions, (
        "worker/loop.py no longer suppresses MaxStepsExceeded; the worker dies on "
        "an over-budget run again"
    )
    for node in suppressions:
        dumped = ast.dump(node)
        assert "Exception" not in dumped and "BaseException" not in dumped, (
            "the MaxStepsExceeded suppression is a blanket catch; a programming "
            "error would now be swallowed too"
        )
