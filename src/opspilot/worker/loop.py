"""The claim-and-drive poll loop.

Responsibility: repeatedly claim the oldest claimable run
(``SELECT ... WHERE status IN (claimable) ORDER BY created_at FOR UPDATE SKIP
LOCKED LIMIT 1``), drive it to a terminal or parked state through the runtime,
and sleep ``WORKER_POLL_INTERVAL`` between polls. On ``RunParked`` the worker
releases the row and moves on -- ``WAITING_APPROVAL`` is the one non-terminal
state it does not hold.

Layer: ``worker``.

Restart behaviour is deliberately shallow: on boot, runs left mid-flight
(``CLASSIFYING``, ``RETRIEVING``, ``PLANNING``, ``EXECUTING``, ``RESPONDING``)
are marked ``FAILED`` with ``failure_reason='interrupted'``; runs in
``WAITING_APPROVAL`` are left alone. Automatic mid-step resume is Phase 2 and is
not pretended here (``docs/architecture.md`` §5).
"""

from __future__ import annotations

import asyncio

from opspilot.domain.runs import AgentRun


async def claim_next(*, worker_id: str) -> AgentRun | None:
    """Atomically claim the oldest claimable run, or ``None``. M0 stub."""
    raise NotImplementedError


async def poll_forever(*, worker_id: str, poll_interval: float) -> None:
    """Loop: claim, drive, sleep. Never returns under normal operation. M0 stub."""
    raise NotImplementedError


async def drain_once(*, worker_id: str) -> bool:
    """Claim and drive a single run; ``True`` if one was processed.

    The loop body, exposed separately so tests drive the worker deterministically
    without sleeping. M0 stub.
    """
    raise NotImplementedError


def sleep(seconds: float) -> asyncio.Future[None]:
    """Awaitable sleep, factored out so tests can patch it. M0 stub."""
    raise NotImplementedError
