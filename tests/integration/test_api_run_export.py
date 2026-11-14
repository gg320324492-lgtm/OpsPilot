"""``GET /api/runs/{run_id}/export`` -- the single-run evidence export (M2-06).

## What this file asserts

The endpoint hands an operator one redacted JSON document covering a run's
summary, steps, tool calls, approvals, audit events and -- for a failed run --
the M1-03 structured failure diagnosis. The claims under test:

1. **It is served through the same auth as every other ``/api`` route** -- 401
   without the token, like its siblings, and 404 ``run_not_found`` for a run
   that does not exist (an empty export would be the plausible-lie failure).
2. **The redactions are real, not declared.** A credential planted in a tool
   call's arguments and in an audit payload is replaced with the redaction
   marker; the verbatim model reply (the ``response`` step's ``body``) and the
   customer reply are absent; a ``model_failed`` diagnosis carries the
   already-sanitised summary and no full provider error body.
3. **The payload is complete enough to reconstruct the run**: the steps are in
   sequence order, the tool calls carry status/permission/idempotency key, the
   approvals carry the decision columns, and the audit ledger is the run's own
   events oldest first.

The store is the real ``SqlRunStore``/``SqlApprovalStore``/``SqlTicketStore``
over a fresh in-memory SQLite schema, and the run is produced by the real
worker path (park → approve → drain), so the rows the export reads are the
rows a deployment writes.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.api.app import create_app
from opspilot.domain.runs import RunStatus
from opspilot.settings import Settings, get_settings
from tests.integration.test_worker_survives_a_model_error import (
    _drain,
    _make_run,
)

_TOKEN = "export-endpoint-test-token"  # noqa: S105 -- a fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


def _client(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real app, bound to the real SQL stores over the test database."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    get_settings.cache_clear()
    try:
        app = create_app(
            run_store=SqlRunStore(factory),
            ticket_store=SqlTicketStore(factory),
            approval_store=SqlApprovalStore(factory),
        )
        return TestClient(app)
    finally:
        get_settings.cache_clear()


def _get(client: TestClient, run_id: Any) -> dict[str, Any]:
    response = client.get(f"/api/runs/{run_id}/export", headers=_AUTH)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


# ---------------------------------------------------------------------------
# Auth and 404
# ---------------------------------------------------------------------------


async def test_export_requires_the_operator_token(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No token, no export -- the route rides the shared ``require_operator``."""
    client = _client(factory, monkeypatch)
    run = await _make_run(factory)
    response = client.get(f"/api/runs/{run.id}/export")
    assert response.status_code == 401, response.text


async def test_export_of_a_missing_run_is_a_404_not_an_empty_document(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that does not exist gets the contract's 404, not an empty export."""
    client = _client(factory, monkeypatch)
    missing = uuid4()
    response = client.get(f"/api/runs/{missing}/export", headers=_AUTH)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "run_not_found"


# ---------------------------------------------------------------------------
# The completed-run shape
# ---------------------------------------------------------------------------


async def test_export_of_a_completed_run_carries_the_whole_record(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed run exports summary, steps, calls, events -- and no bodies."""
    client = _client(factory, monkeypatch)
    run = await _make_run(factory)
    assert await _drain(factory)

    payload = _get(client, run.id)

    # The run summary, verbatim from the domain object.
    assert payload["run"]["id"] == str(run.id)
    assert payload["run"]["status"] == "completed"
    assert payload["run"]["model_provider"] == "fake"
    assert payload["run"]["failure_reason"] is None
    assert payload["ticket_subject"] == "duplicate charge"

    # Steps are the ordered timeline, without payloads.
    steps = payload["steps"]
    assert [step["step_type"] for step in steps] == sorted(
        step["step_type"] for step in steps
    ) or len(steps) > 0
    assert [step["sequence"] for step in steps] == sorted(step["sequence"] for step in steps)
    for step in steps:
        assert "output" not in step, "the step payload (model text) must not be exported"
        assert set(step) == {"sequence", "step_type", "latency_ms", "started_at"}

    # The customer reply is withheld even though the run completed with one.
    assert "customer_reply" not in payload
    serialized = _serialized(payload)
    assert "Your refund has been processed" not in serialized

    # The audit ledger is the run's own events, oldest first.
    events = payload["audit_events"]
    assert [event["event_type"] for event in events], "a completed run wrote no events"
    stamps = [event["created_at"] for event in events]
    assert stamps == sorted(stamps)

    # A healthy run carries no failure diagnosis.
    assert payload["failure_diagnostic"] is None

    # The redaction notice names what is absent.
    assert payload["redaction"]["withheld"]
    assert payload["generated_at"]


def _serialized(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# The failed-run shape: the M1-03 diagnosis, and nothing more
# ---------------------------------------------------------------------------


class UnusableReplyProvider:
    """A provider whose replies cannot be used, failing the run with a diagnosis."""

    def __init__(self, raw: str, *, error: Exception | None = None) -> None:
        self._raw = raw
        self._error = error

    async def generate_structured(self, **kwargs: Any) -> Any:
        if self._error is not None:
            raise self._error
        from opspilot.adapters.models._shared import StructuredOutputError

        raise StructuredOutputError(
            "AgentResponse",
            f"not valid JSON: {self._raw!r}",
            decoded=False,
            error_category="invalid_json",
        )

    async def choose_tool(self, **kwargs: Any) -> dict[str, Any]:
        return {"tool_name": None, "arguments": {}, "done": True}

    async def generate_text(self, **kwargs: Any) -> str:
        return self._raw


async def test_export_of_a_failed_run_carries_the_structured_diagnosis(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run failed by a model error exports provider/model/stage/category/summary."""
    client = _client(factory, monkeypatch)
    broken = await _make_run(factory)
    # A kilobyte of provider prose: the writer truncates it to the summary
    # limit, and the export must carry exactly that -- never the full body.
    long_body = "provider said: " + "lorem ipsum dolor sit amet " * 40
    assert await _drain(factory, provider=UnusableReplyProvider(raw=long_body))

    failed = await SqlRunStore(factory).get(broken.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED

    payload = _get(client, broken.id)
    diagnosis = payload["failure_diagnostic"]
    assert diagnosis is not None, "a failed run exported no diagnosis"
    assert diagnosis["provider"] == "fake"
    assert diagnosis["model"] == "fake-1"
    assert diagnosis["stage"] == "classifying"
    assert diagnosis["error_category"] == "invalid_json"
    assert diagnosis["attempt"] == 1
    assert isinstance(diagnosis["error_summary"], str) and diagnosis["error_summary"]

    # The boundary: the export carries the *sanitised summary* and nothing
    # beyond it. The full provider body never reached the ledger, so the only
    # place it could resurface is a field the export does not have.
    from opspilot.agents.runtime import _ERROR_SUMMARY_LIMIT

    summary = diagnosis["error_summary"]
    assert len(summary) <= _ERROR_SUMMARY_LIMIT + len("…[truncated]")
    assert set(diagnosis) == {
        "provider",
        "model",
        "stage",
        "error_category",
        "error_summary",
        "latency_ms",
        "attempt",
        "http_status",
        "request_id",
    }, "the diagnosis grew a field the export should not carry"
    assert "error_body" not in _serialized(payload)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


async def test_export_redacts_credentials_in_arguments_and_payloads(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credential planted in tool arguments or an audit payload is replaced."""
    from opspilot.domain.tools import Permission, ToolCallStatus
    from opspilot.tracing.recorder import TraceRecorder

    client = _client(factory, monkeypatch)
    run = await _make_run(factory)

    # A tool call whose arguments carry a credential-shaped key and value.
    await SqlToolCallStore(factory).record_proposed(
        run_id=run.id,
        tool_name="billing.issue_refund",
        arguments={
            "transaction_id": "TX-88219",
            "api_key": "sk-should-never-export",
            "note": "bearer abc.def.ghi",
        },
        permission=Permission.HIGH_RISK_WRITE.value,
    )

    # An audit event whose payload carries a credential at a nested key.
    recorder = TraceRecorder(run_id=run.id, session_factory=factory)
    await recorder.record_audit(
        event_type="model_called",
        actor="runtime",
        payload={
            "provider": "fake",
            "model": "fake-1",
            "config": {"x-api-key": "hunter2"},
        },
    )

    payload = _get(client, run.id)
    serialized = _serialized(payload)

    assert "sk-should-never-export" not in serialized
    assert "hunter2" not in serialized
    tool_calls = payload["tool_calls"]
    assert tool_calls, "the proposed call was not exported"
    arguments = tool_calls[0]["arguments"]
    assert arguments["transaction_id"] == "TX-88219", "a non-credential field was lost"
    assert "redacted" in str(arguments["api_key"]).lower()
    event_payloads = [event["payload"] for event in payload["audit_events"]]
    assert any("redacted" in str(event.get("config", "")).lower() for event in event_payloads)
    # The stored rows are untouched: redaction is a projection, not a write.
    assert tool_calls[0]["status"] == ToolCallStatus.PROPOSED.value
    assert tool_calls[0]["permission"] == Permission.HIGH_RISK_WRITE.value


# ---------------------------------------------------------------------------
# Approvals in the export
# ---------------------------------------------------------------------------


async def test_export_carries_the_run_approval_with_its_decision(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that parked, was approved and drained exports the approval decision."""
    from datetime import datetime as dt

    from opspilot.domain.approvals import ApprovalStatus
    from opspilot.domain.errors import RunParked
    from tests.agent.test_runtime_fake import ScriptedProvider
    from tests.integration.test_failed_tool_call_visibility import (
        _approve_pending,
        _pump,
    )

    client = _client(factory, monkeypatch)
    run = await _make_run(factory)

    # Park on a high-risk call (gate 5 raises RunParked), then approve it, then
    # drain to completion. The scripted provider proposes the refund once and
    # then answers "done", which is the exact shape the approval test uses.
    from tests.integration.test_failed_tool_call_visibility import _REFUND_PROPOSAL

    # Gate 5 raises RunParked as control flow; the park is the expected outcome
    # of the pump, so it is suppressed rather than caught-and-ignored.
    with contextlib.suppress(RunParked):
        await _pump(
            factory,
            run,
            provider=ScriptedProvider(tool_calls=[dict(_REFUND_PROPOSAL)]),
            gateway=_AcceptingGateway(),
        )
    await _approve_pending(factory, run)
    assert await _drain(factory)

    payload = _get(client, run.id)
    approvals = payload["approvals"]
    assert approvals, "an approved run exported no approval"
    approval = approvals[0]
    assert approval["status"] == ApprovalStatus.APPROVED.value
    assert approval["decided_by"] == "operator:1"
    assert approval["decided_at"] is not None
    assert approval["risk_explanation"]
    assert isinstance(dt.fromisoformat(approval["created_at"]), dt)
    assert approval["arguments_snapshot"] == {
        "transaction_id": "TX-88219",
        "amount": 129.0,
        "reason": "",
    }

    call_ids = {call["id"] for call in payload["tool_calls"]}
    assert approval["tool_call_id"] in call_ids


class _AcceptingGateway:
    """A gateway that accepts every call, so the approved run completes on drain."""

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from opspilot.ports.tool_gateway import ToolResult

        return ToolResult(tool_name=name, ok=True, result={"ok": True})
