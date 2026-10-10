"""The worker survives a model that cannot be reached. One run dies, not the queue.

## Why this file exists

Reproduced live on the Compose stack, with ``MODEL_PROVIDER=anthropic`` and a
gateway that would not accept a connection. The ``APIConnectionError`` from
``_respond`` propagated ``drain_once`` -> ``poll_forever`` -> ``main()``'s
``poll.result()`` and killed the worker process; the container restarted, and the
boot sweep marked **every** run the worker had been holding
``FAILED('interrupted')``:

    opspilot-worker[worker-compose]: stopped
      File ".../worker/loop.py", line 236, in drain_once
      File ".../agents/runtime.py", line 1248, in _respond
      File ".../adapters/models/anthropic_provider.py", line 113, in generate_structured
    httpx2.ConnectError -> anthropic.APIConnectionError: Connection error.
    opspilot-worker[worker-compose]: booted; marked 1 interrupted run(s) failed; polling

Nothing in the test suite caught it, because every provider in the suite is a
scripted fake or a recorded fixture: the only provider that can *fail to connect*
is a real one. That is the failure mode this file closes -- a green suite and a
broken worker, with the difference living entirely in code the tests never ran.

## What is asserted

* an always-raising provider does not kill the drain: ``drain_once`` returns, and
  the run is left where ``worker/loop.py``'s policy says it should be --
  ``FAILED`` with a reason naming the upstream, not ``interrupted``;
* the worker is still alive afterwards: the *next* run drains to ``COMPLETED``
  with a working provider;
* a programming error still escapes and kills the worker. That is the half of the
  policy that makes the other half safe, so it is pinned rather than assumed.

The exceptions are the **real SDK classes**, constructed by the SDK itself. A
hand-rolled ``APIConnectionError`` lookalike would have to duplicate the
hierarchy this catch is defined against, and would keep passing after that
hierarchy changed -- which is the same green-tests/broken-worker gap this file
exists to close.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.model_provider import ModelProvider, ModelResponse
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder
from opspilot.worker.loop import (
    MODEL_REJECTED,
    MODEL_UNAVAILABLE,
    _model_failure_errors,
    _model_failure_reason,
    drain_once,
)
from tests.agent.test_runtime_fake import ScriptedProvider, UnusedOrchestrator

# The URL the SDK error objects carry. It is never dialled -- the exception is
# constructed, not raised by a client -- so it only has to be a plausible endpoint.
_ENDPOINT = "https://api.example.invalid/v1/messages"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def _make_run(
    factory: sessionmaker[Session],
    *,
    status: RunStatus = RunStatus.RECEIVED,
) -> AgentRun:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="duplicate charge", body="charged twice", customer_email="a@example.com"
        )
        run = await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )
        if status is not RunStatus.RECEIVED:
            await SqlRunStore(session).set_status(run.id, status)
            run.status = status
        return run


def _sdk_error(sdk: str, attribute: str) -> BaseException:
    """Build the SDK's own exception, the way the SDK builds it for a dead gateway.

    Not a lookalike defined in this file. ``getattr`` on the imported module means
    a rename or a re-parenting in the SDK turns this into an ``AttributeError``
    here, in a test, rather than into an uncaught crash in a worker.
    """
    import httpx

    module = pytest.importorskip(sdk)
    # ``Any`` rather than a class annotation: the constructor signatures are
    # the SDK's own and differ per class (``request``, ``response``/``body``).
    error_class: Any = getattr(module, attribute)
    request = httpx.Request("POST", _ENDPOINT)
    if attribute.endswith("TimeoutError"):
        return cast(BaseException, error_class(request=request))
    if issubclass(error_class, module.APIStatusError):
        # A status error carries the response it was built from. The status code
        # does not drive the classification -- the class does -- so 401 is fine
        # for ``PermissionDeniedError`` too.
        return cast(
            BaseException,
            error_class(
                "provider refused the request",
                response=httpx.Response(401, request=request),
                body=None,
            ),
        )
    return cast(BaseException, error_class(request=request, message="Connection error."))


WEB_UNREACHABLE = [
    pytest.param("anthropic", "APIConnectionError", id="anthropic-connection"),
    pytest.param("openai", "APIConnectionError", id="openai-connection"),
    pytest.param("anthropic", "APITimeoutError", id="anthropic-timeout"),
    pytest.param("openai", "APITimeoutError", id="openai-timeout"),
]


class RaisingProvider:
    """A provider whose every call raises the same provider-SDK exception."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    async def generate_structured(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        schema: type[Any],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> ModelResponse[Any]:
        self.calls += 1
        raise self.error

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.calls += 1
        raise self.error

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        self.calls += 1
        raise self.error


class BrokenProvider:
    """A provider with a programming error inside it: what must kill the worker."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        schema: type[Any],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> ModelResponse[Any]:
        self.calls += 1
        # The shape of a real defect rather than an environment that is down:
        # code that is wrong.
        raise TypeError("generate_structured() missing 1 required positional argument")

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.calls += 1
        raise TypeError("choose_tool() got an unexpected keyword argument")

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        self.calls += 1
        raise TypeError("generate_text() got an unexpected keyword argument")


class RecordingGateway:
    """Records the tools the pump asked for; returns canned successes.

    Local rather than shared, because no run here reaches a tool: every provider
    in this file raises before the gateway is consulted. Keeping it local means a
    future edit to the shared one cannot change what this test measures.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True})


def _recorder_factory(factory: sessionmaker[Session]) -> Callable[[UUID], TraceRecorder]:
    return lambda run_id: TraceRecorder(run_id=run_id, session_factory=factory)


async def _drain(
    factory: sessionmaker[Session],
    *,
    provider: ModelProvider | None = None,
) -> bool:
    """Drive one poll iteration, exactly as ``poll_forever`` calls it."""
    return await drain_once(
        worker_id="w1",
        run_store=SqlRunStore(factory),
        ticket_store=SqlTicketStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        provider=provider if provider is not None else ScriptedProvider(),
        gateway=RecordingGateway(),
        orchestrator=UnusedOrchestrator(),
        recorder_factory=_recorder_factory(factory),
        retrieval=None,
    )


def _audit_payloads(
    factory: sessionmaker[Session], run_id: UUID, event_type: str
) -> list[dict[str, object]]:
    with db.session_scope(factory) as session:
        rows = (
            session.execute(
                select(models.AuditEvent).where(
                    models.AuditEvent.run_id == run_id,
                    models.AuditEvent.event_type == event_type,
                )
            )
            .scalars()
            .all()
        )
    return [dict(row.payload or {}) for row in rows]


# ---------------------------------------------------------------------------
# The defect: one dropped model call used to kill the whole queue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("sdk", "attribute"), WEB_UNREACHABLE)
async def test_an_unreachable_model_fails_one_run_and_spares_the_worker(
    factory: sessionmaker[Session],
    sdk: str,
    attribute: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The drain survives, and the run is failed *honestly*.

    ``FAILED('model_unavailable')`` and not ``FAILED('interrupted')``: the reason
    recorded is the reason it died. Before the fix the only way this run got a
    reason at all was the boot sweep's blanket ``interrupted`` -- the word for
    "the process died", which says nothing about a gateway that was already down.
    """
    broken = await _make_run(factory)

    with caplog.at_level(logging.ERROR, logger="opspilot.worker.loop"):
        processed = await _drain(factory, provider=RaisingProvider(_sdk_error(sdk, attribute)))
    assert processed is True, "the drain raised instead of returning True"

    failed = await SqlRunStore(factory).get(broken.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.failure_reason == MODEL_UNAVAILABLE

    payloads = _audit_payloads(factory, broken.id, "run_failed")
    assert payloads, "a run that died left no audit event"
    assert payloads[0]["failure_reason"] == MODEL_UNAVAILABLE

    # The log line the incident did not have: the worker's whole output for the
    # real failure was one line -- the boot line.
    lines = [record.getMessage() for record in caplog.records]
    assert any("model call failed" in line and str(broken.id) in line for line in lines), (
        "the run died and nothing was logged; an operator would have to find it in the database"
    )


@pytest.mark.parametrize(("sdk", "attribute"), WEB_UNREACHABLE)
async def test_the_worker_drains_the_next_run_after_a_model_error(
    factory: sessionmaker[Session],
    sdk: str,
    attribute: str,
) -> None:
    """The point of the whole change: the worker is *still running* afterwards.

    Two runs, the older one broken and the newer one fine, which also makes this
    the blast-radius assertion -- one model failure costs one run, where the
    incident cost every run the worker was holding.
    """
    broken = await _make_run(factory)
    healthy = await _make_run(factory)

    assert await _drain(factory, provider=RaisingProvider(_sdk_error(sdk, attribute)))

    # A second drain with a working provider, as the next poll cycle would be.
    # If the first drain had raised, this line would not be reached.
    assert await _drain(factory)

    survived = await SqlRunStore(factory).get(healthy.id)
    assert survived is not None
    assert survived.status is RunStatus.COMPLETED, (
        "the next run did not complete after a model error; the worker did not survive"
    )

    still_broken = await SqlRunStore(factory).get(broken.id)
    assert still_broken is not None
    assert still_broken.status is RunStatus.FAILED


async def test_a_model_error_at_the_respond_step_is_still_one_run(
    factory: sessionmaker[Session],
) -> None:
    """The step the live stack actually died in: ``RESPONDING``.

    ``_respond`` transitions to ``RESPONDING`` *before* calling the model, so a
    failure there leaves the run in the one status whose only legal successors
    are ``COMPLETED`` and ``FAILED``. This asserts the failing write is legal from
    there rather than assuming it -- ``RESPONDING -> FAILED`` is in the table
    (``docs/agent-state-machine.md`` §2), and a policy that quietly relied on an
    edge that was not would fail the ``set_status`` write, not the test.
    """
    responding = await _make_run(factory, status=RunStatus.RESPONDING)

    assert await _drain(
        factory, provider=RaisingProvider(_sdk_error("anthropic", "APIConnectionError"))
    )

    failed = await SqlRunStore(factory).get(responding.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.failure_reason == MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# A refused identity is a different failure from an unreachable model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sdk", ["anthropic", "openai"])
async def test_an_authentication_error_names_the_credential_not_the_network(
    factory: sessionmaker[Session],
    sdk: str,
) -> None:
    """A 401 is a key problem; a connection error is a network problem.

    Both fail the run, but they send different people to different places. A
    worker reporting one reason for both would send an operator to check the
    gateway while the key was revoked.
    """
    run = await _make_run(factory)

    assert await _drain(factory, provider=RaisingProvider(_sdk_error(sdk, "AuthenticationError")))

    failed = await SqlRunStore(factory).get(run.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.failure_reason == MODEL_REJECTED


# ---------------------------------------------------------------------------
# The other half of the policy: a programming error still kills the worker
# ---------------------------------------------------------------------------


async def test_a_programming_error_still_escapes_and_kills_the_worker(
    factory: sessionmaker[Session],
) -> None:
    """The safety property of the catch, pinned.

    A catch broad enough to swallow a ``TypeError`` would let the worker keep
    polling in a state that is already wrong, marking runs failed for a defect in
    OpsPilot's own code -- and nothing would notice the defect underneath. So the
    worker dies, loudly, and this is the assertion that it still does.
    """
    await _make_run(factory)

    with pytest.raises(TypeError):
        await _drain(factory, provider=BrokenProvider())


# ---------------------------------------------------------------------------
# The classification itself, against the real SDK hierarchies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sdk", ["anthropic", "openai"])
def test_a_specific_error_class_beats_its_base(sdk: str) -> None:
    """The reason comes from the raised class, not from the first table match.

    ``anthropic.AuthenticationError`` is an ``APIStatusError``, which is an
    ``APIError``. Walking the MRO is what makes it ``model_rejected`` instead of
    ``model_unavailable``; a lookup over a list would get this wrong the moment
    someone reordered the list.
    """
    pytest.importorskip(sdk)
    assert _model_failure_reason(_sdk_error(sdk, "AuthenticationError")) == MODEL_REJECTED
    assert _model_failure_reason(_sdk_error(sdk, "APIConnectionError")) == MODEL_UNAVAILABLE
    assert _model_failure_reason(_sdk_error(sdk, "APITimeoutError")) == MODEL_UNAVAILABLE
    assert _model_failure_reason(_sdk_error(sdk, "PermissionDeniedError")) == MODEL_REJECTED
    # The ancestor is reached only when nothing more specific is: a 400 or a 500
    # is the provider answering, not the provider being absent.
    assert _model_failure_reason(_sdk_error(sdk, "InternalServerError")) == MODEL_UNAVAILABLE


def test_both_provider_sdks_contribute_to_the_catch() -> None:
    """Neither SDK may be missing from the catch.

    ``MODEL_PROVIDER`` is a runtime setting, so a catch that resolved only
    ``anthropic`` would stay invisible until a deployment switched provider --
    the same shape as the missing-SDK defect ``__main__`` refuses at boot.
    """
    modules = {cls.__module__.split(".")[0] for cls in _model_failure_errors()}
    for sdk in ("anthropic", "openai"):
        pytest.importorskip(sdk)
        assert sdk in modules, f"{sdk} contributes no exception class to the catch"


def test_a_programming_error_is_not_in_the_catch() -> None:
    """The negative half, asserted rather than assumed.

    ``TypeError``/``AttributeError``/``KeyError`` must stay out of the tuple, or
    the test above -- "the worker dies on a defect" -- would be the one that is
    quietly wrong.
    """
    errors = _model_failure_errors()
    for programming_error in (TypeError, AttributeError, KeyError, ValueError):
        assert programming_error not in errors, (
            f"{programming_error.__name__} is caught; the worker would survive a bug"
        )
