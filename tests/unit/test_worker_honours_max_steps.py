"""``MAX_STEPS`` must reach the ``RunContext`` the worker builds.

## Why this file exists

``MAX_STEPS`` was declared in ``settings.py``, documented in ``.env.example``,
passed into both compose services by the previous fix, and read by **nothing**.
The chain stopped one layer below compose:

```
settings.max_steps = 7          <- reachable, verified inside the container
RunContext.max_steps built by the worker = 24   <- a literal in agents/state.py
```

``worker/loop.py::_build_context`` constructed ``RunContext(...)`` without a
``max_steps=`` argument, so the dataclass default in ``agents/state.py`` always
applied and ``agents/runtime.py`` read *that*. The recorded consequence was a
real end-to-end run that spent all 24 steps on read calls (``get_invoice`` x12,
``list_transactions`` x9, ``issues.create`` x3), never proposed
``billing.issue_refund``, and ended ``max_steps_exceeded`` -- while ``MAX_STEPS=40``
sat in the ``.env``, having reached the process and changed nothing.

This is the fourth recurrence of one shape: a setting that is *configured*,
*reachable*, and *consulted by nothing*. Every other guard tests what the code
does with a value; this one tests whether the value arrives at all.

## What is asserted

1. ``_build_context`` puts the configured value on the context -- **both** of its
   return paths, because the missing-ticket fallback builds a second, different
   ``RunContext`` and wiring only the happy path leaves the malformed-row branch
   on the stale literal. That is the same defect, one branch in.
2. The default is still ``24``, so nothing that relied on it moved.
3. There is exactly **one** definition of the budget. This was a *three*-copy
   defect, not a two-copy one: ``settings.py``, ``agents/state.py`` and
   ``agents/runtime.py`` each held a literal ``24``. The first fix would have
   wired the worker and left two stale copies behind, which is precisely what
   M6a found for ``RETRIEVAL_MIN_SCORE``.
4. ``MAX_STEPS=40`` reaches the runtime's *effective* budget, not just the
   dataclass field -- because ``run_loop`` reads ``ctx.max_steps or
   _DEFAULT_MAX_STEPS``, so a context carrying a stale value silently wins over
   the setting. Asserted through ``run_loop`` itself for that reason.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from opspilot.agents.state import RunContext
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.stores import RunStore, TicketStore
from opspilot.settings import get_settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opspilot"


class _Ticket:
    """The fields ``_build_context`` reads off the port's ticket row."""

    def __init__(self, ticket_id: UUID) -> None:
        self.id = ticket_id
        self.subject = "Duplicate charge"
        self.body = "Charged twice for TX-88219."
        self.customer_email = "customer@example.com"


class _TicketStore:
    """A ``TicketStore`` stub returning one ticket, or ``None`` on request."""

    def __init__(self, *, missing: bool = False) -> None:
        self._missing = missing

    async def get(self, ticket_id: UUID) -> _Ticket | None:
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


async def _build(*, missing: bool = False) -> RunContext:
    from opspilot.worker.loop import _build_context

    store = _TicketStore(missing=missing)
    # The stub implements the one method ``_build_context`` calls and nothing
    # else, so it cannot satisfy the whole ``TicketStore`` port -- which is
    # correct: the port's other methods are not what is under test here, and
    # implementing them would add unread code to a file about a step budget. The
    # cast states that narrowing at the one place it is made, rather than
    # sprinkling ``# type: ignore`` through the file.
    return await _build_context(_run(), ticket_store=cast("TicketStore", store))


async def _build_recording(
    monkeypatch: pytest.MonkeyPatch, *, missing: bool = False
) -> dict[str, Any]:
    """``_build_context``, capturing the kwargs it hands ``RunContext``.

    Recording the constructor call rather than only reading the result is what
    makes the wiring observable. Reading ``ctx.max_steps`` alone cannot tell the
    worker passing the budget from the dataclass default supplying it, because
    both now resolve to ``settings.max_steps`` -- so removing the argument would
    change nothing observable, and this test would keep passing over exactly the
    defect it exists to catch. What the *worker* does is a fact about the
    constructor call, and that is what is asserted.
    """
    from opspilot.worker import loop

    captured: dict[str, Any] = {}
    # The loop module *uses* ``RunContext`` (that is why monkeypatching the name
    # on it is what intercepts the construction) without re-exporting it in
    # ``__all__``, so the attribute read needs the narrowing. What is asserted is
    # the constructor call on that module object, which is exactly what patching
    # it intercepts.
    real = loop.RunContext  # type: ignore[attr-defined]

    def _spy(**kwargs: object) -> RunContext:
        captured.update(kwargs)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(loop, "RunContext", _spy)
    captured["ctx"] = await _build(missing=missing)
    return captured


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", [False, True], ids=["ticket_found", "ticket_missing"])
async def test_worker_passes_the_configured_budget_to_the_context(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    """``MAX_STEPS=40`` must be *passed* on every ``RunContext`` the worker builds."""
    monkeypatch.setenv("MAX_STEPS", "40")
    get_settings.cache_clear()
    try:
        captured = await _build_recording(monkeypatch, missing=missing)
    finally:
        get_settings.cache_clear()

    assert "max_steps" in captured, (
        "_build_context did not pass max_steps to RunContext. The dataclass "
        "default happens to yield the same number today, so no assertion on the "
        "resulting context would notice -- but the worker wiring is the thing "
        "under test, and it is what regressed: MAX_STEPS reached no RunContext "
        "the worker built, and a run ended max_steps_exceeded while MAX_STEPS=40 "
        "sat in .env."
    )
    assert captured["max_steps"] == 40
    assert captured["max_steps"] == get_settings().max_steps
    assert captured["ctx"].max_steps == 40


@pytest.mark.parametrize("missing", [False, True], ids=["ticket_found", "ticket_missing"])
async def test_the_budget_is_not_the_literal_twenty_four(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    """Pin the specific number the bug produced, so the fix cannot be a no-op.

    A test asserting only "40 is passed through" would also pass if the code
    passed through some *other* wrong value; asserting the two together says
    the value came from settings and not from a literal.
    """
    monkeypatch.setenv("MAX_STEPS", "40")
    get_settings.cache_clear()
    try:
        captured = await _build_recording(monkeypatch, missing=missing)
    finally:
        get_settings.cache_clear()

    assert captured["max_steps"] != 24
    assert captured["ctx"].max_steps == get_settings().max_steps


async def test_the_default_is_still_twenty_four(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset ``MAX_STEPS`` keeps the shipped default, so nothing else moves."""
    monkeypatch.delenv("MAX_STEPS", raising=False)
    get_settings.cache_clear()
    try:
        captured = await _build_recording(monkeypatch)
    finally:
        get_settings.cache_clear()

    assert captured["max_steps"] == 24
    assert get_settings().max_steps == 24
    assert captured["ctx"].max_steps == 24


# ---------------------------------------------------------------------------
# One definition
# ---------------------------------------------------------------------------

# Modules allowed to *name* the budget. ``settings.py`` defines it; the other
# two must reference it.
_ALLOWED = frozenset({"settings.py"})


def _budget_literals() -> list[tuple[str, int, int]]:
    """Every int literal under ``src/`` assigned to a max_steps-shaped name.

    Found by parsing rather than grepping, so a value spelled ``0x18`` is caught
    too, and so the fix -- which *references* the setting -- does not match.
    """
    found: list[tuple[str, int, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Assign | ast.AnnAssign):
                raw = node.targets if isinstance(node, ast.Assign) else [node.target]
                targets = [t.id for t in raw if isinstance(t, ast.Name)]
                value = node.value
            else:
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, int):
                continue
            if isinstance(value.value, bool):
                continue
            if not any("MAX_STEPS" in n or "max_steps" in n for n in targets):
                continue
            found.append((path.name, node.lineno, value.value))
    return found


def test_the_budget_has_exactly_one_definition() -> None:
    """No module under ``src/`` may carry its own literal for the step budget.

    This was a *three*-copy defect -- ``settings.py``, ``agents/state.py`` and
    ``agents/runtime.py`` -- so a fix that only added a read would have left two
    stale copies in place. That is the whole defect family: a value an operator
    can move in one place and not the others.
    """
    offenders = [entry for entry in _budget_literals() if entry[0] not in _ALLOWED]
    assert not offenders, (
        f"the step budget is defined as a literal in more than one place: {offenders}. "
        "Each copy is a budget a deployment can raise in one file and not the "
        "others -- which is how MAX_STEPS=40 produced a 24-step run. Reference "
        "Settings.max_steps instead, via agents.state.DEFAULT_MAX_STEPS."
    )


def test_the_two_derived_defaults_agree_with_settings() -> None:
    """Both fallbacks resolve to the setting, not a remembered number.

    Asserted by constructing each one *after* the cache is cleared, because the
    fix is that neither is a frozen import-time constant: a ``Final[int] =
    _settings_max_steps()`` would read ``MAX_STEPS`` once for the life of the
    process and be just as stale as the literal it replaced, which is a test
    that only passes because it happens to run in the right order.
    """
    from opspilot.agents.runtime import _DEFAULT_MAX_STEPS
    from opspilot.agents.state import default_max_steps

    configured = get_settings().max_steps
    assert default_max_steps() == configured
    assert _DEFAULT_MAX_STEPS() == configured


def test_the_default_follows_the_setting_after_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback is resolved per use, not captured when the module loaded.

    This is the failure mode a module-level constant would have: the value is
    frozen at import, so a deployment that changes ``MAX_STEPS`` -- or a test
    that changes it to prove the wiring -- keeps getting the old number.
    """
    from opspilot.agents.state import default_max_steps

    monkeypatch.setenv("MAX_STEPS", "11")
    get_settings.cache_clear()
    try:
        assert default_max_steps() == 11
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("MAX_STEPS", "12")
    get_settings.cache_clear()
    try:
        assert default_max_steps() == 12
    finally:
        get_settings.cache_clear()


def test_runtime_does_not_redefine_the_budget_from_a_literal() -> None:
    """``runtime._DEFAULT_MAX_STEPS`` must reference the shared resolver.

    Structural rather than by value: a resolver and a literal that happens to
    return the same number are indistinguishable by comparison, and only the
    resolver survives someone changing the setting.
    """
    source = (SRC / "agents" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="runtime.py")
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and "_DEFAULT_MAX_STEPS" in ast.dump(node)
        and isinstance(node.value, ast.Call | ast.Name)
    ]
    assert assignments, "runtime.py no longer defines _DEFAULT_MAX_STEPS"
    assert "default_max_steps" in source, (
        "runtime.py reads a budget without consulting agents.state.default_max_steps -- "
        "it is holding its own copy again"
    )


def test_state_does_not_redefine_the_budget_from_a_literal() -> None:
    """``RunContext.max_steps``'s default is the shared resolver, not a number."""
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
            and kw.value.id == "default_max_steps"
            for kw in node.keywords
        )
    ]
    assert calls, (
        "RunContext.max_steps must use field(default_factory=default_max_steps); a "
        "literal there is the defect this file exists to prevent"
    )


# ---------------------------------------------------------------------------
# The runtime honours the context, not just the dataclass
# ---------------------------------------------------------------------------


async def test_run_loop_uses_the_contexts_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A context carrying a non-default budget must be the budget enforced.

    ``run_loop`` computes ``ctx.max_steps or _DEFAULT_MAX_STEPS()``. A stale
    context therefore wins over the setting *silently* -- which is the direction
    this defect ran in, and the reason wiring the dataclass alone would not have
    been enough to trust.

    Driven through the ``proposals`` path, which tests the budget before it calls
    the model at all (``agents/runtime.py``: ``if ctx.steps_taken >= max_steps``
    guards the loop), so a pass here is about the budget and not about a stub
    provider happening to answer.
    """
    from unittest.mock import AsyncMock, MagicMock

    from opspilot.agents.runtime import run_loop
    from opspilot.agents.state import RunContext
    from opspilot.domain.errors import MaxStepsExceeded

    # Deliberately a *different* number from the context's, so the assertion
    # below distinguishes "the context won" from "the setting happened to match".
    monkeypatch.setenv("MAX_STEPS", "24")
    get_settings.cache_clear()
    try:
        ctx = RunContext(
            run=_run(),
            ticket_subject="Duplicate charge",
            ticket_body="Charged twice.",
            customer_email="customer@example.com",
            max_steps=1,
        )
        ctx.steps_taken = 1  # already at this context's budget of 1

        recorder = MagicMock()
        recorder.record_audit = AsyncMock()
        recorder.record_step = AsyncMock()

        with pytest.raises(MaxStepsExceeded) as excinfo:
            await run_loop(
                ctx,
                [],  # the proposals path: budget checked before any model call
                provider=MagicMock(),
                gateway=MagicMock(),
                orchestrator=MagicMock(),
                run_store=cast("RunStore", _RunStore()),
                tool_call_store=MagicMock(),
                approval_store=MagicMock(),
                recorder=recorder,
            )
    finally:
        get_settings.cache_clear()

    assert excinfo.value.max_steps == 1, (
        "run_loop enforced a budget other than the context's, so a MAX_STEPS the "
        "worker carried would not reach the loop that applies it"
    )


class _RunStore:
    """Enough of ``RunStore`` for ``run_loop``'s budget-failure path."""

    def __init__(self) -> None:
        self.statuses: list[tuple[UUID, RunStatus, dict[str, object]]] = []

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
    ) -> None:
        self.statuses.append((run_id, status, {"failure_reason": failure_reason}))
