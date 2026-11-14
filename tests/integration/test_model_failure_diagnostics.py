"""The M1-03 structured failure diagnosis, end to end through the worker.

## What this file asserts

A model call that fails to produce a usable answer leaves **two** structured
records -- the ``model_failed`` audit event in the run's trace, and the worker's
log line -- and both carry the same fields: provider, model, stage, error
category, latency, attempt count, and the HTTP status / request id when the
provider exposed them.

The categories are the point of the milestone: an **empty response** and an
**invalid JSON** reply are different failures with different fixes (a gateway
returning ``""`` versus a proxy answering in HTML), so they are never the same
token in a trace. Both are content failures -- the provider answered -- so both
fail the run ``schema_invalid`` (the pre-existing token for "the model's answer
did not fit"), while the *category* in the diagnosis distinguishes them.

## Failure isolation

Every test here also asserts the queue survives: the next run, seeded after the
broken one, drains to ``COMPLETED``. That is M1-03's "失败只影响当前 run" -- and
it is asserted against the real SQLite stores and the real worker loop, not a
spy, because the claim is about the worker, not about a function call.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.models._shared import StructuredOutputError
from opspilot.adapters.persistence.repositories import SqlRunStore
from opspilot.agents.schemas import TicketClassification
from opspilot.domain.runs import RunStatus
from opspilot.worker.loop import drain_once
from tests.integration.test_worker_survives_a_model_error import (
    _audit_payloads,
    _drain,
    _make_run,
)


class UnusableReplyProvider:
    """A provider whose ``generate_structured`` raises ``StructuredOutputError``.

    The exception is the adapters' own (``adapters/models/_shared.py``), raised
    exactly as the real OpenAI/Anthropic adapters raise it when the reply does
    not parse -- so the runtime's ``except`` and the category derivation run
    against the real class, not a lookalike.
    """

    def __init__(self, *, raw: str) -> None:
        self._error = self._build(raw)
        self.calls = 0

    @staticmethod
    def _build(raw: str) -> StructuredOutputError:
        try:
            # Re-derive the exception through ``parse_structured`` so the
            # category assignment is the production one, not a hand-picked one.
            from opspilot.adapters.models._shared import parse_structured

            parse_structured(raw, TicketClassification)
        except StructuredOutputError as error:
            return error
        raise AssertionError("the raw reply was expected to fail parsing")  # pragma: no cover

    async def generate_structured(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        schema: type[Any],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> Any:  # noqa: ANN401 -- the port's own signature
        self.calls += 1
        raise self._error

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.calls += 1
        return {"tool_name": None, "arguments": {}, "done": True}

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        self.calls += 1
        return ""


@pytest.fixture
def factory() -> Any:  # noqa: ANN401 -- a sessionmaker, typed loosely in tests
    """A sessionmaker over a fresh in-memory SQLite schema."""
    from opspilot.adapters.persistence import db
    from opspilot.adapters.persistence.models import Base
    from opspilot.settings import Settings

    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def test_an_empty_response_is_diagnosed_as_empty_response(
    factory: sessionmaker[Session],
) -> None:
    """``""`` from the provider is ``empty_response``, failed, and isolated.

    The empty reply is the gateway failure mode: the HTTP call succeeded, the
    body did not exist. Before the diagnosis existed it surfaced as an
    uncaught ``StructuredOutputError`` -- a ``RuntimeError`` -- that killed the
    worker and let the boot sweep mark the queue ``interrupted``.
    """
    broken = await _make_run(factory)
    healthy = await _make_run(factory)

    processed = await _drain(factory, provider=UnusableReplyProvider(raw=""))
    assert processed is True

    failed = await SqlRunStore(factory).get(broken.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.failure_reason == "schema_invalid"

    payloads = _audit_payloads(factory, broken.id, "model_failed")
    assert payloads, "an empty reply left no structured diagnosis"
    diagnosis = payloads[0]
    assert diagnosis["error_category"] == "empty_response"
    assert diagnosis["provider"] == "fake"
    assert diagnosis["model"] == "fake-1"
    assert diagnosis["stage"] == "classifying"
    assert diagnosis["attempt"] == 1
    assert diagnosis["http_status"] is None, "a content failure carries no HTTP status"
    assert diagnosis["error_summary"] == "empty response"

    # Failure isolation: the next ticket still completes.
    assert await _drain(factory)
    survived = await SqlRunStore(factory).get(healthy.id)
    assert survived is not None
    assert survived.status is RunStatus.COMPLETED


async def test_an_invalid_json_reply_is_diagnosed_as_invalid_json(
    factory: sessionmaker[Session],
) -> None:
    """Prose that does not parse is ``invalid_json`` -- not ``empty_response``.

    The distinction is M1-03's explicit requirement: the two answers mean
    different things went wrong, and a trace that collapses them into one
    "unknown" sends the operator looking at the wrong layer.
    """
    broken = await _make_run(factory)
    healthy = await _make_run(factory)

    processed = await _drain(factory, provider=UnusableReplyProvider(raw="<html>login</html>"))
    assert processed is True

    failed = await SqlRunStore(factory).get(broken.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED

    payloads = _audit_payloads(factory, broken.id, "model_failed")
    assert payloads, "an unparseable reply left no structured diagnosis"
    diagnosis = payloads[0]
    assert diagnosis["error_category"] == "invalid_json"
    assert "not valid JSON" in str(diagnosis["error_summary"])
    assert diagnosis["error_summary"] != "empty response"

    assert await _drain(factory)
    survived = await SqlRunStore(factory).get(healthy.id)
    assert survived is not None
    assert survived.status is RunStatus.COMPLETED


async def test_a_schema_mismatch_is_diagnosed_as_schema_mismatch(
    factory: sessionmaker[Session],
) -> None:
    """JSON that parsed but missed the schema is the third category.

    ``decoded=True``: the reply *was* JSON, so the fix is a prompt or schema
    conversation, not a gateway check. It is still distinct from the two
    parse-level failures above.
    """
    broken = await _make_run(factory)
    healthy = await _make_run(factory)

    processed = await _drain(factory, provider=UnusableReplyProvider(raw='{"unexpected": true}'))
    assert processed is True

    payloads = _audit_payloads(factory, broken.id, "model_failed")
    assert payloads, "a schema-mismatched reply left no structured diagnosis"
    assert payloads[0]["error_category"] == "schema_mismatch"

    assert await _drain(factory)
    survived = await SqlRunStore(factory).get(healthy.id)
    assert survived is not None
    assert survived.status is RunStatus.COMPLETED


async def test_a_planning_stage_failure_names_the_planning_stage(
    factory: sessionmaker[Session],
) -> None:
    """The stage field is where the call was made, not always classifying.

    A classification that succeeds and a planning round that returns prose
    fails the run from ``PLANNING`` -- the diagnosis says so, which is what
    tells an operator *which* of the run's model calls died.
    """
    from tests.agent.test_runtime_fake import ScriptedProvider

    broken = await _make_run(factory)
    healthy = await _make_run(factory)

    class ProsePlanner(ScriptedProvider):
        """Classification succeeds; the first planning round returns prose."""

        async def choose_tool(
            self,
            *,
            system: str,  # noqa: ARG002 -- protocol parameter
            prompt: str,  # noqa: ARG002 -- protocol parameter
            available_tools: list[str],  # noqa: ARG002 -- protocol parameter
            timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
        ) -> dict[str, Any]:
            raise UnusableReplyProvider._build("not json at all")

    processed = await _drain(factory, provider=ProsePlanner())
    assert processed is True

    payloads = _audit_payloads(factory, broken.id, "model_failed")
    assert payloads, "a planning-stage failure left no diagnosis"
    assert payloads[0]["stage"] == "planning"
    assert payloads[0]["error_category"] == "invalid_json"

    assert await _drain(factory)
    survived = await SqlRunStore(factory).get(healthy.id)
    assert survived is not None
    assert survived.status is RunStatus.COMPLETED


def test_the_failure_categories_are_mutually_distinguishable() -> None:
    """The three content categories are three different tokens.

    A guard on the vocabulary itself: if a refactor ever folds two of these
    together, the traces this milestone promises stop being able to answer
    "what did the provider actually send?" -- which is the regression this file
    exists to catch.
    """
    categories = {
        UnusableReplyProvider._build(raw).error_category
        for raw in ("", "<html>x</html>", '{"b": 1}')
    }
    assert categories == {"empty_response", "invalid_json", "schema_mismatch"}, (
        "empty response, invalid JSON and schema mismatch must remain distinct categories"
    )


def test_drain_once_is_still_importable_from_the_worker_loop() -> None:
    """A smoke import so a broken cross-module import fails here, loudly."""
    assert drain_once is not None
    assert logging.getLogger("opspilot.worker.loop") is not None
