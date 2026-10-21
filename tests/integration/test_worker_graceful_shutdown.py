"""A shutdown must not kill the run in flight.

## Why this file exists

``worker/__main__.py`` claimed, in three places and once in bold, that "the
stop is a poll boundary, never a mid-step kill", and that a run in flight is
"driven to its own terminal or parked status first".

**The claim was not executable.** ``poll_forever`` had no stop parameter at
all, its loop body never read one, and the only exit path was
``task.cancel()`` on the polling task -- which delivers ``CancelledError`` to
whatever the loop is awaiting, and during a drive that is the model call, the
tool dispatch or the database write.

So a ``Ctrl-C``, a ``docker compose down`` or a Kubernetes eviction could cut
a refund dispatch in half. **The money was safe** -- the idempotency key is
deterministic from ``(run_id, transaction_id)`` and the billing server replays
the original refund -- but the **run** was not: it was left in a
claim-and-work status, and the next boot's sweep marked it
``FAILED('interrupted')``, which is not what "the stop is a poll boundary"
says happens.

## What this file asserts

1. The loop returns at once when the stop is already set -- it does not claim
   a run it is not going to drive.
2. A stop that arrives **during** a drive lets that drive finish: the
   boundary is the end of the drive, not the start of it.
3. The entry point does not cancel the polling task when the signal arrives.
   It waits for the loop to reach its boundary, bounded by a grace period.

The third is the one that fails against the old code, and it fails for the
right reason: the old entry point cancelled the task, and the cancellation is
visible as a ``CancelledError`` inside the loop.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from opspilot.ports.model_provider import ModelProvider
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.stores import (
    ApprovalStore,
    CitationStore,
    RunStore,
    TicketStore,
    ToolCallStore,
)
from opspilot.ports.tool_gateway import ToolGateway
from opspilot.worker import __main__ as worker_main
from opspilot.worker.loop import poll_forever


def _loop_arguments(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 -- the collaborators differ per test
    """The keyword arguments ``poll_forever`` needs, most of them unused here.

    The collaborators are ``AsyncMock``s because the loop never reaches them
    in these tests: the stop is checked before the first claim, and the drive
    is stubbed out.
    """
    arguments: dict[str, Any] = {
        "worker_id": "worker-shutdown-test",
        "run_store": cast(RunStore, AsyncMock()),
        "ticket_store": cast(TicketStore, AsyncMock()),
        "tool_call_store": cast(ToolCallStore, AsyncMock()),
        "approval_store": cast(ApprovalStore, AsyncMock()),
        "provider": cast(ModelProvider, AsyncMock()),
        "gateway": cast(ToolGateway, AsyncMock()),
        "orchestrator": cast(Orchestrator, AsyncMock()),
        "poll_interval": 0.01,
    }
    arguments.update(overrides)
    return arguments


async def test_the_loop_returns_at_once_when_the_stop_is_already_set() -> None:
    """A stop that arrived before the loop started claims nothing.

    Claiming a run and then not driving it is how a run ends up in a
    claim-and-work status with nobody holding it.
    """
    stop = asyncio.Event()
    stop.set()
    run_store_mock = AsyncMock()

    with patch("opspilot.worker.loop.drain_once", AsyncMock()) as drain:
        await poll_forever(**_loop_arguments(run_store=cast(RunStore, run_store_mock), stop=stop))

    drain.assert_not_awaited()
    run_store_mock.claim_next.assert_not_awaited()


async def test_a_stop_that_arrives_mid_drive_finishes_the_drive_first() -> None:
    """The poll boundary is the end of the drive, not the start of it.

    The fake drive sets the stop *while it is running* and then does more
    work. If the loop cancelled the drive, the work after ``stop.set()``
    would never happen -- and that is exactly the "mid-step kill" the entry
    point's docstring promised would not happen.
    """
    stop = asyncio.Event()
    finished = False

    async def fake_drain_once(**_kwargs: object) -> bool:
        nonlocal finished
        stop.set()
        # Work that a cancellation would skip.
        await asyncio.sleep(0)
        finished = True
        return True

    with patch("opspilot.worker.loop.drain_once", fake_drain_once):
        await poll_forever(**_loop_arguments(stop=stop))

    assert finished, (
        "the drive was cut short by the stop; the boundary is the end of the "
        "drive, and a run in flight must reach its own status"
    )


async def test_a_stop_that_arrives_during_the_sleep_is_noticed_at_once() -> None:
    """The wait between polls is interruptible.

    A plain sleep would keep the loop asleep for a whole poll interval after
    the signal arrived, and a poll interval is a *deployment* setting -- a
    worker configured to poll every 30 seconds would ignore a ``Ctrl-C`` for
    half a minute. The stop is an event precisely so that the wait can be
    woken by it.
    """
    stop = asyncio.Event()
    driving = asyncio.Event()

    async def fake_drain_once(**_kwargs: object) -> bool:
        driving.set()
        return False  # nothing to do: the loop goes to sleep

    with patch("opspilot.worker.loop.drain_once", fake_drain_once):
        running = asyncio.ensure_future(
            poll_forever(**_loop_arguments(stop=stop, poll_interval=30.0))
        )
        await asyncio.wait_for(driving.wait(), timeout=5)
        stop.set()
        # A thirty-second poll interval, and the loop is out in five seconds.
        await asyncio.wait_for(running, timeout=5)


async def test_the_entry_point_waits_for_the_loop_instead_of_cancelling_it() -> None:
    """A signal does not cancel the poll loop; it lets the loop stop itself.

    The old entry point waited for whichever of the two futures finished and
    then cancelled the other -- so a signal cancelled the polling task, which
    is a mid-step kill wearing a different name. This asserts the opposite:
    the loop reaches its own boundary and returns, and nothing cancels it on
    the way.
    """
    cancelled = False

    async def fake_poll_forever(*, stop: asyncio.Event | None = None, **_kwargs: object) -> None:
        nonlocal cancelled
        try:
            if stop is None:
                # A loop that was given no stop event can only end by being
                # cancelled -- which is the old behaviour, and the thing this
                # test exists to catch.
                await asyncio.Event().wait()
            assert stop is not None
            await stop.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    worker = worker_main._Worker(
        worker_id="worker-shutdown-test",
        session_factory=AsyncMock(),
        run_store=cast(RunStore, AsyncMock()),
        ticket_store=cast(TicketStore, AsyncMock()),
        tool_call_store=cast(ToolCallStore, AsyncMock()),
        approval_store=cast(ApprovalStore, AsyncMock()),
        provider=cast(ModelProvider, AsyncMock()),
        gateway=cast(ToolGateway, AsyncMock()),
        orchestrator=cast(Orchestrator, AsyncMock()),
        recorder_factory=cast(Any, AsyncMock()),
        retrieval=cast(Any, AsyncMock()),
        citation_store=cast(CitationStore, AsyncMock()),
        poll_interval=0.01,
    )

    with (
        patch.object(worker_main, "poll_forever", fake_poll_forever),
        patch.object(worker_main, "mark_interrupted_on_boot", lambda _factory: 0),
    ):
        running = asyncio.ensure_future(worker_main._run(worker))
        # Let the loop start, then deliver the signal a deployment sends.
        # ``raise_signal`` rather than ``os.kill``: on Windows ``os.kill`` with
        # ``SIGINT`` *terminates* the process, and this test must not take the
        # test runner with it. ``raise_signal`` runs the Python-level handler,
        # which is the one the entry point installs.
        await asyncio.sleep(0.05)
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(running, timeout=10)

    assert not cancelled, (
        "the entry point cancelled the poll loop on a signal, which delivers "
        "CancelledError to whatever the loop is awaiting -- a mid-step kill"
    )
