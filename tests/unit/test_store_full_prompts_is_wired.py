"""``STORE_FULL_PROMPTS`` must actually withhold the model's text.

## Why this file exists

``STORE_FULL_PROMPTS`` was declared in ``settings.py``, documented in
``.env.example``, passed into both compose services, and read by **nothing**. The
setting's only other appearance in the source tree was a sentence in
``tracing/recorder.py``'s module docstring asserting what Phase 1 *does*:

    "Phase 1 stores the full prompt and response in ``agent_steps.input``/
    ``output`` by design (``STORE_FULL_PROMPTS``)"

That sentence was false about the code it described, in a way worth stating
plainly because it decided how this was fixed. **No prompt was ever written to
any trace row.** Every ``record_step`` payload in ``agents/runtime.py`` is
structured metadata::

    {provider, model}              classification, input
    {category, confidence}         classification, output
    {count, document_slugs}        retrieval
    {tool_name, done}              planning
    {from_status, to_status}       state_change
    {escalated, chars, body}       response  <- the only verbatim model text

So the docstring was describing an unimplemented idea and, in doing so,
mis-describing the present: it claimed a behaviour that did not exist rather than
the one that did. The fix therefore wires the switch to what is actually stored --
the model's *response* -- and corrects the docstring, rather than implementing
prompt storage in order to make the sentence true.

## What is asserted

1. ``True`` (the default): the reply is stored verbatim. Nothing changes.
2. ``False``: the model's words are replaced, and **everything else survives** --
   the step exists, its ``chars`` and ``escalated`` are intact, the row's other
   fields are untouched. A withheld step is still a countable step.
3. Only full-text keys are withheld. ``{from_status, to_status}`` and
   ``{tool_name, done}`` are the state machine and the plan; a blanket "strip the
   strings" rule would empty exactly those and produce a trace that cannot answer
   any question.
4. Audit payloads are untouched, because none of them carries model text and
   ``approval_requested``'s ``arguments`` is what a human approves.
5. ``STORE_FULL_PROMPTS`` has exactly one reader outside ``settings.py``, so the
   switch cannot be satisfied by a second, disagreeing copy.
"""

from __future__ import annotations

import ast
import pathlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from opspilot.settings import get_settings
from opspilot.tracing import recorder as recorder_module

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    # The concrete type the recorder's ``session_factory`` port takes.
    Factory = sessionmaker[Session]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opspilot"

# The reply the runtime records, and the marker that replaces it.
_BODY = "We have issued a full refund of $129.00 for TX-88219."


@pytest.fixture
def factory() -> Factory:
    """An in-memory database every recorder in this file writes to.

    The recorder must be handed this same factory (rather than building its own
    over the same URL) or the rows would land in a *different* empty database --
    the reason ``TraceRecorder`` documents ``session_factory``.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from opspilot.adapters.persistence import models

    engine = create_engine("sqlite+pysqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _recorder(factory: Factory) -> recorder_module.TraceRecorder:
    return recorder_module.TraceRecorder(run_id=uuid4(), session_factory=factory)


def _steps(factory: Factory) -> list[dict[str, Any]]:
    """Every recorded step, in order, projected to plain dicts.

    Projected *inside* the session: returning the ORM objects detaches them when
    the scope closes, and every attribute access afterwards raises
    ``DetachedInstanceError``. The repo's other step-reading tests
    (``tests/agent/test_step_latency.py``) do the same projection.

    ``Any`` rather than ``object`` for the row values: ``row.output`` is a JSON
    column the assertions index into (``step["output"]["body"]``), and typing it
    as ``object`` makes every such read a type error rather than a read. The
    projection is the boundary where the ORM's JSON columns become plain Python,
    so ``Any`` is the honest type there -- not at the assertion.
    """
    from sqlalchemy import select

    from opspilot.adapters.persistence import db, models

    with db.session_scope(factory) as session:
        rows = session.execute(
            select(models.AgentStep).order_by(models.AgentStep.sequence)
        ).scalars()
        return [
            {
                "step_type": row.step_type,
                "input": row.input,
                "output": row.output,
                "latency_ms": row.latency_ms,
                "sequence": row.sequence,
            }
            for row in rows
        ]


def _events(factory: Factory) -> list[dict[str, Any]]:
    """Every recorded audit event, projected to plain dicts.

    ``Any`` for the same reason as :func:`_steps`: the payload is a JSON column
    these tests index into, and the projection is where it stops being one.
    """
    from sqlalchemy import select

    from opspilot.adapters.persistence import db, models

    with db.session_scope(factory) as session:
        rows = session.execute(select(models.AuditEvent)).scalars()
        return [{"event_type": row.event_type, "payload": row.payload} for row in rows]


def _set(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setenv("STORE_FULL_PROMPTS", "true" if value else "false")
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


async def test_the_reply_is_stored_when_the_setting_is_on(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """The default is unchanged: the model's own words land in the row."""
    _set(monkeypatch, True)
    try:
        await _recorder(factory).record_step(
            step_type="response",
            output_payload={"escalated": False, "chars": len(_BODY), "body": _BODY},
            latency_ms=412,
        )
    finally:
        get_settings.cache_clear()

    steps = _steps(factory)
    assert len(steps) == 1
    assert steps[0]["output"]["body"] == _BODY
    assert steps[0]["latency_ms"] == 412


async def test_the_reply_is_withheld_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """``STORE_FULL_PROMPTS=false`` stops the model's words being persisted."""
    _set(monkeypatch, False)
    try:
        await _recorder(factory).record_step(
            step_type="response",
            output_payload={"escalated": False, "chars": len(_BODY), "body": _BODY},
            latency_ms=412,
        )
    finally:
        get_settings.cache_clear()

    steps = _steps(factory)
    assert len(steps) == 1
    stored = dict(steps[0]["output"])
    assert _BODY not in str(stored), "the reply body was persisted despite STORE_FULL_PROMPTS=false"
    assert stored["body"] == recorder_module._WITHHELD


async def test_the_step_is_still_complete_when_the_text_is_withheld(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """The event keeps everything except the text.

    This is the half of the switch that is easy to get wrong. Withholding the
    reply must not withhold *the fact that a reply happened*, nor its length,
    nor the escalation flag -- ``api/routers/runs.py`` renders the trace step
    from ``chars`` and ``escalated``, and ``evals/runner.py`` reads
    ``escalated`` off the same payload. A redaction that emptied the row would
    pass "the body is not stored" and fail every one of those readers.
    """
    _set(monkeypatch, False)
    try:
        await _recorder(factory).record_step(
            step_type="response",
            output_payload={"escalated": True, "chars": len(_BODY), "body": _BODY},
            latency_ms=412,
        )
    finally:
        get_settings.cache_clear()

    step = _steps(factory)[0]
    assert step["step_type"] == "response"
    assert step["output"]["chars"] == len(_BODY)
    assert step["output"]["escalated"] is True
    assert step["latency_ms"] == 412
    assert step["sequence"] == 1
    assert "body" in step["output"], (
        "the key was dropped rather than replaced; repositories.get_customer_reply "
        "reads output['body'] to serve RunDetail.customer_reply (docs/api-contract.md "
        "§3), so a missing key breaks a documented reader"
    )


async def test_non_text_payloads_are_untouched(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """Only full-text keys are withheld; the structural fields are the trace.

    ``{from_status, to_status}`` is the state machine and ``{tool_name, done}``
    is the plan. A blanket "strip every string" implementation would empty
    precisely these, leaving a trace that cannot answer a single question -- the
    same failure ``limitations.md`` describes from the other side, "a trace you
    cannot read is not a trace".
    """
    _set(monkeypatch, False)
    try:
        recorder = _recorder(factory)
        await recorder.record_state_change(from_status="planning", to_status="executing")
        await recorder.record_step(
            step_type="planning", output_payload={"tool_name": "billing.get_invoice", "done": False}
        )
    finally:
        get_settings.cache_clear()

    steps = {step["step_type"]: step for step in _steps(factory)}
    assert steps["state_change"]["output"]["to_status"] == "executing"
    assert steps["state_change"]["output"]["from_status"] == "planning"
    assert steps["planning"]["output"]["tool_name"] == "billing.get_invoice"
    assert steps["planning"]["output"]["done"] is False


async def test_a_prompt_key_would_be_withheld_too(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """The switch covers ``prompt``, which no caller writes today.

    Named in ``_FULL_TEXT_KEYS`` now so that the day a step starts recording the
    prompt -- which is what the old docstring claimed already happened -- it is
    covered by the switch rather than by whoever remembers to check.
    """
    _set(monkeypatch, False)
    try:
        await _recorder(factory).record_step(
            step_type="planning",
            input_payload={"prompt": "Subject: duplicate charge ...", "model": "fake-1"},
        )
    finally:
        get_settings.cache_clear()

    output = _steps(factory)[0]["input"]
    assert "duplicate charge" not in str(output)
    assert output["prompt"] == recorder_module._WITHHELD
    assert output["model"] == "fake-1", "a non-text sibling field must survive"


async def test_the_callers_payload_is_not_mutated(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """Redaction copies the payload; the runtime reuses its own dict.

    ``_respond`` builds ``output_payload`` and then reads ``ctx.response_body``
    from its own state, but a caller that passed a dict it still holds must not
    find it rewritten under it.
    """
    _set(monkeypatch, False)
    payload = {"escalated": False, "chars": len(_BODY), "body": _BODY}
    try:
        await _recorder(factory).record_step(step_type="response", output_payload=payload)
    finally:
        get_settings.cache_clear()

    assert payload["body"] == _BODY


async def test_audit_payloads_are_not_redacted(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """``record_audit`` is deliberately outside the switch, and this pins why.

    The one free-text field in an audit payload is ``approval_requested``'s
    ``arguments`` -- the exact payload a human approves
    (``docs/tool-permissions.md`` §3.2), mirrored into the ledger so the record
    of *what was authorised* survives. Redacting the evidence of an approval is
    not a privacy control, it is the loss of the control's audit trail.
    """
    _set(monkeypatch, False)
    try:
        await _recorder(factory).record_audit(
            event_type="approval_requested",
            actor="runtime",
            payload={
                "tool_call_id": str(uuid4()),
                "tool_name": "billing.issue_refund",
                "risk_explanation": "This moves $129.00 and cannot be undone automatically.",
                "arguments": {"transaction_id": "TX-88219", "amount": 129.00},
            },
        )
    finally:
        get_settings.cache_clear()

    payload = _events(factory)[0]["payload"]
    assert payload["arguments"]["transaction_id"] == "TX-88219"
    assert payload["risk_explanation"].startswith("This moves")


async def test_a_none_payload_is_still_none(
    monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> None:
    """``record_step`` with no payloads writes nulls, not empty dicts."""
    _set(monkeypatch, False)
    try:
        await _recorder(factory).record_step(
            step_type="state_change", output_payload={"to_status": "x"}
        )
    finally:
        get_settings.cache_clear()

    step = _steps(factory)[0]
    assert step["input"] is None
    assert step["output"] == {"to_status": "x"}


# ---------------------------------------------------------------------------
# One reader
# ---------------------------------------------------------------------------


def test_the_setting_has_exactly_one_reader() -> None:
    """No module under ``src/`` but ``settings.py`` may read the flag.

    The whole defect family is a value that lives in more places than it is
    consulted. Two readers of the same flag is where the next disagreeing copy
    starts, so the count is asserted rather than assumed.
    """
    readers: list[tuple[str, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "settings.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "store_full_prompts"
                and not (isinstance(node.value, ast.Name) and node.value.id in {"self", "resolved"})
            ):
                readers.append((str(path.relative_to(REPO_ROOT)), node.lineno))

    assert len(readers) == 1, (
        f"expected exactly one reader of Settings.store_full_prompts, found {readers}. "
        "Two readers is where a second, disagreeing copy of the switch starts."
    )
    assert readers[0][0].replace("\\", "/").endswith("tracing/recorder.py")
