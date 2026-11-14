"""``REJECTED_PROPOSAL_STOP_LIMIT`` must reach the ``RunContext`` the worker builds.

## Why this file exists

Phase 3 M2-02 added the consecutive-identical-invalid-proposal stop, and with
it a new settings knob. The ``MAX_STEPS`` defect
(``test_worker_honours_max_steps.py``) is the template this repo keeps
re-meeting: a setting that is *declared*, *documented*, and *consulted by
nothing* on the path that matters -- because ``_build_context`` built a
``RunContext`` without passing it and a dataclass default answered instead.
This file holds the new knob to the same standard the first time, rather than
after a live incident proves the wiring missing.

## What is asserted

1. ``_build_context`` passes ``rejected_proposal_stop_limit`` to the
   ``RunContext`` constructor -- **both** return paths (ticket found / missing),
   the same two-branch requirement the budget test pins.
2. The default stays ``3``.
3. There is exactly one definition: no module under ``src/`` carries its own
   literal for the threshold, and the dataclass default is the shared resolver
   (``agents.state.default_rejected_proposal_stop_limit``), resolved per
   construction rather than frozen at import.
4. The value follows the setting after import.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any, cast
from uuid import uuid4

import pytest

from opspilot.agents.state import (
    RunContext,
    default_rejected_proposal_stop_limit,
)
from opspilot.domain.runs import AgentRun
from opspilot.ports.stores import TicketStore
from opspilot.settings import get_settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opspilot"


class _Ticket:
    """The fields ``_build_context`` reads off the port's ticket row."""

    def __init__(self, ticket_id: Any) -> None:
        self.id = ticket_id
        self.subject = "Duplicate charge"
        self.body = "Charged twice."
        self.customer_email = "customer@example.com"


class _TicketStore:
    """A ``TicketStore`` stub returning one ticket, or ``None`` on request."""

    def __init__(self, *, missing: bool = False) -> None:
        self._missing = missing

    async def get(self, ticket_id: Any) -> _Ticket | None:
        if self._missing:
            return None
        return _Ticket(ticket_id)


def _run() -> AgentRun:
    """A minimal ``AgentRun``; ``_build_context`` only reads ``id``/``ticket_id``."""
    return AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        model_provider="fake",
        model_name="fake-1",
    )


async def _build_recording(
    monkeypatch: pytest.MonkeyPatch, *, missing: bool = False
) -> dict[str, Any]:
    """``_build_context``, capturing the kwargs it hands ``RunContext``.

    Recording the constructor call rather than only reading the result is what
    makes the wiring observable -- see ``test_worker_honours_max_steps.py`` for
    the full argument: the dataclass default and the setting resolve to the
    same number today, so only the constructor call distinguishes "the worker
    passed it" from "the default answered".
    """
    from opspilot.worker import loop

    captured: dict[str, Any] = {}
    real = loop.RunContext  # type: ignore[attr-defined]

    def _spy(**kwargs: object) -> RunContext:
        captured.update(kwargs)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(loop, "RunContext", _spy)

    store = _TicketStore(missing=missing)
    # Same narrowing as the budget test: the stub implements the one method
    # ``_build_context`` calls, and the cast states it once, here.
    captured["ctx"] = await loop._build_context(_run(), ticket_store=cast("TicketStore", store))
    return captured


@pytest.mark.parametrize("missing", [False, True], ids=["ticket_found", "ticket_missing"])
async def test_worker_passes_the_configured_limit_to_the_context(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    """``REJECTED_PROPOSAL_STOP_LIMIT=7`` must be *passed* on every context."""
    monkeypatch.setenv("REJECTED_PROPOSAL_STOP_LIMIT", "7")
    get_settings.cache_clear()
    try:
        captured = await _build_recording(monkeypatch, missing=missing)
    finally:
        get_settings.cache_clear()

    assert "rejected_proposal_stop_limit" in captured, (
        "_build_context did not pass rejected_proposal_stop_limit to RunContext. "
        "The dataclass default happens to yield the same number today, so no "
        "assertion on the resulting context would notice -- but the worker "
        "wiring is the thing under test, and the MAX_STEPS defect is the "
        "template this repeats."
    )
    assert captured["rejected_proposal_stop_limit"] == 7
    assert captured["ctx"].rejected_proposal_stop_limit == 7


async def test_the_default_is_still_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset ``REJECTED_PROPOSAL_STOP_LIMIT`` keeps the shipped default of 3."""
    monkeypatch.delenv("REJECTED_PROPOSAL_STOP_LIMIT", raising=False)
    get_settings.cache_clear()
    try:
        captured = await _build_recording(monkeypatch)
    finally:
        get_settings.cache_clear()

    assert captured["rejected_proposal_stop_limit"] == 3
    assert get_settings().rejected_proposal_stop_limit == 3
    assert captured["ctx"].rejected_proposal_stop_limit == 3


def test_the_threshold_has_exactly_one_definition() -> None:
    """No module under ``src/`` may carry its own literal for the threshold.

    The ``MAX_STEPS`` defect was a *three*-copy one; this pins the new knob at
    one copy from day one.
    """
    found: list[tuple[str, int, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            raw = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in raw if isinstance(t, ast.Name)]
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, int):
                continue
            if isinstance(value.value, bool):
                continue
            if not any("rejected_proposal" in n or "REJECTED_PROPOSAL" in n for n in names):
                continue
            found.append((path.name, node.lineno, value.value))
    offenders = [entry for entry in found if entry[0] != "settings.py"]
    assert not offenders, (
        f"the repeat-invalid threshold is defined as a literal outside "
        f"settings.py: {offenders}. Each copy is a threshold a deployment can "
        "move in one file and not the others. Reference "
        "Settings.rejected_proposal_stop_limit instead."
    )


def test_the_dataclass_default_uses_the_shared_resolver() -> None:
    """``RunContext.rejected_proposal_stop_limit`` defaults to the resolver."""
    source = (SRC / "agents" / "state.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="state.py")
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "field"
        and any(
            kw.arg == "default_factory"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "default_rejected_proposal_stop_limit"
            for kw in node.keywords
        )
    ]
    assert calls, (
        "RunContext.rejected_proposal_stop_limit must use "
        "field(default_factory=default_rejected_proposal_stop_limit); a literal "
        "there is the MAX_STEPS defect wearing a new name"
    )


def test_the_default_follows_the_setting_after_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resolver is resolved per use, not captured when the module loaded."""
    monkeypatch.setenv("REJECTED_PROPOSAL_STOP_LIMIT", "5")
    get_settings.cache_clear()
    try:
        assert default_rejected_proposal_stop_limit() == 5
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("REJECTED_PROPOSAL_STOP_LIMIT", "6")
    get_settings.cache_clear()
    try:
        assert default_rejected_proposal_stop_limit() == 6
    finally:
        get_settings.cache_clear()
