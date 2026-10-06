"""Worker process entry point.

Responsibility: the ``opspilot-worker`` console script and ``python -m
opspilot.worker``. Wires concrete adapters, marks interrupted runs on boot, then
runs the poll loop until a shutdown signal.

Layer: ``worker`` (composition root). This is the one place the worker chooses
concrete adapters -- the loop itself depends only on the ports and the runtime.

**Retrieval wiring (M5c).** The worker builds the retrieval stack from settings
and passes it to :func:`poll_forever`. ``knowledge.search`` is not an MCP server
(``docs/mcp-contracts.md`` §4), so retrieval is built in-process -- the embedder,
the vector store and the ``RETRIEVAL_MIN_SCORE`` threshold -- and threaded through
as the runtime's published ``RetrievalCallable``. A worker that passed ``None``
would still record the RETRIEVING step, but with zero hits and no citations, so
the run would have no Sources panel. It also passes ``retrieval_min_score``
explicitly: the loop's own default is read from settings at import time, and
passing the value the *process* was configured with is what makes a deployment
that lowered the threshold in one place see it everywhere.

**The process, not just the loop (M7).** ``poll_forever`` is only the inner
loop. Everything that makes it a *service* lives here:

- the concrete adapter graph is assembled from settings, once;
- interrupted runs are swept on boot (``mark_interrupted_on_boot``);
- ``SIGINT``/``SIGTERM`` stop the loop at the next poll boundary rather than
  killing the process mid-step.

Shutdown is deliberately *not* a second sweep. A signal arrives between two
``drain_once`` calls at the latest, so the run in flight either finished (its
status is already durable) or is left in a claim-and-work status -- and the next
boot's sweep is what marks it ``FAILED('interrupted')``. Doing it here as well
would risk failing the one status that is deliberately exempt: ``EXECUTING``.
See :func:`_install_signal_handlers` and ``docs/architecture.md`` §5.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from uuid import UUID

from opspilot.settings import get_settings
from opspilot.worker.loop import mark_interrupted_on_boot, poll_forever

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    from opspilot.adapters.wiring import RetrievalStack
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
    from opspilot.tracing.recorder import TraceRecorder

    type RecorderFactory = Callable[[UUID], TraceRecorder]

# The signals a supervisor sends to ask for a clean stop: Ctrl-C, and the
# ``SIGTERM`` every container runtime and init system uses.
_HANDLED_SIGNALS: Final[tuple[signal.Signals, ...]] = (signal.SIGINT, signal.SIGTERM)

# The model each provider falls back to when ``MODEL_NAME`` is blank. Kept here
# rather than in each adapter so the blank-means-default rule has one definition,
# and so an eval recorded against a default names the same model everywhere.
# These are the defaults ``.env.example`` documents.
_DEFAULT_MODEL_NAMES: Final[dict[str, str]] = {
    "fake": "fake-1",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.1",
}


class WorkerConfigError(RuntimeError):
    """The worker cannot start because the deployment is misconfigured.

    A ``RuntimeError`` rather than an ``OpsPilotError``, and raised before the
    loop starts rather than on the first run, for the same reason
    ``MissingOperatorToken`` escapes the app factory: a worker that came up and
    silently did nothing is worse than one that refuses to start. The message
    says what to set, not only what is wrong, so an operator reading the process
    output can fix it without reading the source.
    """

    def __init__(self, detail: str, *, how_to_fix: str) -> None:
        super().__init__(f"{detail}. {how_to_fix}")


def _database_build_errors() -> tuple[type[BaseException], ...]:
    """The exceptions that genuinely mean "the database could not be reached".

    Built dynamically because SQLAlchemy is imported lazily -- this module must
    import without it, so the console script's entry point can report a named
    configuration fault rather than an ``ImportError`` from three frames down.

    The list is deliberately short. Every exception named here produces the
    message that tells an operator to check ``DATABASE_URL``, so anything added
    has to be something the database really causes: a bad URL, an unreachable
    server, a missing driver, or a SQLite path that cannot be opened. A
    programming error -- an ``AttributeError`` from a misspelled function, a
    ``TypeError`` from a bad call -- must fall through and surface as itself.
    Catching it and relabelling it as a database fault is worse than crashing,
    because it sends the reader somewhere the problem is not.
    """
    errors: list[type[BaseException]] = [OSError]  # includes sqlite3.OperationalError's base
    try:
        import sqlalchemy.exc
    except ImportError:  # pragma: no cover - SQLAlchemy is a hard dependency
        return tuple(errors)
    errors.extend(
        [
            sqlalchemy.exc.SQLAlchemyError,
            sqlalchemy.exc.OperationalError,
            sqlalchemy.exc.ArgumentError,
        ]
    )
    return tuple(errors)


#: Resolved once at import so the ``except`` clause is cheap and the tuple is
#: built from whatever SQLAlchemy is actually installed.
_DATABASE_BUILD_ERRORS: Final[tuple[type[BaseException], ...]] = _database_build_errors()


@dataclass(frozen=True)
class _Worker:
    """The wired collaborators :func:`main` hands to the poll loop.

    A named bundle rather than a kwargs dict so every piece the loop needs is
    visible in one place and typed -- the composition root's only job is this
    wiring, and a dict would make a missing collaborator a ``KeyError`` at first
    claim instead of a type error at build time.
    """

    worker_id: str
    session_factory: object
    run_store: RunStore
    ticket_store: TicketStore
    tool_call_store: ToolCallStore
    approval_store: ApprovalStore
    provider: ModelProvider
    gateway: ToolGateway
    orchestrator: Orchestrator
    recorder_factory: RecorderFactory
    retrieval: Callable[[str], object]
    citation_store: CitationStore
    poll_interval: float


def build_worker_retrieval(*, session_factory: object | None = None) -> RetrievalStack:
    """Build the worker's retrieval stack from settings.

    Named rather than inlined so it is the single place the worker's retrieval
    wiring lives and so a test can call it without starting the loop. The adapter
    import is local, matching the entry point's "import concrete adapters only at
    composition time" style.

    ``session_factory`` is threaded in from the worker's own stores so the
    vector store, the ``knowledge_chunks`` rows it writes and the citation rows
    the runtime writes all land in the *same* database. A second factory over
    ``:memory:`` would open a different empty one and every citation would be
    skipped as unresolvable (``adapters/wiring.py``).
    """
    from opspilot.adapters.wiring import build_retrieval_stack

    return build_retrieval_stack(get_settings(), session_factory=session_factory)


def resolve_worker_id(settings: object = None) -> str:
    """The identity this process claims runs under.

    ``WORKER_ID`` defaults to empty (``.env.example``: "Unique per worker
    process"). An empty identity is not a usable one, so it is not passed
    through: whatever the deployment forgot to configure, two workers must still
    be distinguishable from each other.

    What that does and does not protect, stated precisely rather than repeated
    from the setting's comment. ``SqlRunStore.claim_next`` ignores ``worker_id``
    today -- mutual exclusion is the row lock, not the identity (``repositories
    .py``: "the row's holder is the open transaction") -- so an empty id does not
    corrupt a claim *in this implementation*. What it costs is attribution: a run
    stuck mid-flight cannot be traced to the process holding it, and a trace that
    cannot say which worker drove a run is a trace you cannot debug from. The
    fallback is what closes that hole without demanding configuration.

    The fallback is ``worker-<pid>``: unique per process on one host, stable for
    the process's lifetime, and derivable with nothing but the interpreter. It is
    *not* unique across hosts -- a multi-host deployment must set ``WORKER_ID``,
    and this is the honest limit of what can be inferred rather than configured.
    """
    from opspilot.settings import Settings

    resolved = get_settings() if settings is None else settings
    assert isinstance(resolved, Settings)
    configured = resolved.worker_id.strip()
    if configured:
        return configured
    return f"worker-{os.getpid()}"


def build_worker_provider(settings: object = None) -> ModelProvider:
    """Build the ``ModelProvider`` named by ``MODEL_PROVIDER``.

    ``fake`` needs no key and is the default, so a fresh clone and CI run the
    whole golden path with no credentials. The two real adapters are constructed
    here but import their SDK lazily inside their methods, so a deployment that
    never selects them never needs them installed.

    A key that is missing is *not* checked here: ``MODEL_PROVIDER=anthropic``
    with no ``ANTHROPIC_API_KEY`` is a real configuration, and the adapter
    raises ``MissingAPIKeyError`` on first use rather than at startup -- which is
    the right place, because a worker that refuses to boot would also refuse to
    drain the runs that use the fake provider.

    Raises:
        WorkerConfigError: If ``MODEL_PROVIDER`` names a provider this build has
            no adapter for.
    """
    from opspilot.settings import Settings

    resolved = get_settings() if settings is None else settings
    assert isinstance(resolved, Settings)

    # Read as ``str``, not as the ``Literal``: the annotation already guarantees
    # membership, and matching on it would make the fall-through unreachable to
    # the type checker. The raise below is therefore not dead code, it is the
    # guard for the day a name is added to the ``Literal`` and this dispatch is
    # forgotten -- which would otherwise be a worker that starts and answers
    # every run with the wrong provider.
    name = str(resolved.model_provider)
    model_name = resolved.model_name or _DEFAULT_MODEL_NAMES.get(name, name)

    if name == "fake":
        from opspilot.adapters.models.fake import FakeModelProvider

        # The scenario is what makes this provider usable from a real process.
        # It has two answering paths -- a named scenario's script, or matching a
        # request hash against a fixture -- and every shipped fixture records
        # `request_hash: null`, so only the scenario path has data. Until this
        # setting existed, nothing outside the test harness passed one, and a
        # worker built from the defaults claimed a run and watched it die at
        # `classifying` with UnmatchedFixtureError.
        #
        # `or None`, not `or "duplicate_charge"`: an unconfigured deployment must
        # fail loudly on an unmatched request rather than silently replay the
        # refund script against whatever ticket it was given.
        return FakeModelProvider(
            provider_name="fake",
            scenario=resolved.fake_scenario or None,
        )

    if name == "anthropic":
        from opspilot.adapters.models.anthropic_provider import AnthropicModelProvider

        return AnthropicModelProvider(
            api_key=resolved.anthropic_api_key,
            model_name=model_name,
            timeout_seconds=resolved.model_timeout_seconds,
        )

    if name == "openai":
        from opspilot.adapters.models.openai_provider import OpenAIModelProvider

        return OpenAIModelProvider(
            api_key=resolved.openai_api_key,
            model_name=model_name,
            timeout_seconds=resolved.model_timeout_seconds,
        )

    raise WorkerConfigError(
        f"MODEL_PROVIDER={name!r} has no adapter in this build",
        how_to_fix=(
            f"set MODEL_PROVIDER to one of {sorted(_DEFAULT_MODEL_NAMES)} "
            "(see .env.example)"
        ),
    )


def build_worker(settings: object = None) -> _Worker:
    """Assemble every concrete collaborator the poll loop needs.

    Imported lazily so importing this module -- which the console script does at
    process start -- does not pull in SQLAlchemy, the MCP servers or the
    retrieval stack before settings have been read.

    Raises:
        WorkerConfigError: If the persistence layer cannot supply the stores, or
            the named model provider has no adapter.
    """
    from opspilot.adapters.orchestration.linear import LinearOrchestrator
    from opspilot.adapters.persistence import repositories
    from opspilot.adapters.tools.mcp_gateway import MCPToolGateway
    from opspilot.adapters.wiring import build_citation_store
    from opspilot.settings import Settings
    from opspilot.tracing.recorder import TraceRecorder

    resolved = get_settings() if settings is None else settings
    assert isinstance(resolved, Settings)

    # Built here rather than through a shared helper because the API builds the
    # same three stores in ``api/app.py::_build_sql_stores`` and the two want
    # different things from them: the API needs the trio, the worker also needs
    # the ``tool_call_store`` and the session factory that the recorder and the
    # citation store are built from. A helper returning all six would be a
    # function with one caller per call site, which is a rename, not a
    # deduplication.
    #
    # The exception is narrowed to the failures that really are the database.
    # An earlier version caught ``Exception`` and reported every failure as
    # "could not build its stores from DATABASE_URL" -- while the actual fault
    # was a call to ``repositories.build_stores``, a function that does not
    # exist. A deployment with a perfectly good database was told to go and
    # check its database. The message is what an operator acts on, so a broad
    # catch here is not defensive, it is a signpost pointing the wrong way.
    from opspilot.adapters.persistence import db as _db

    try:
        factory = _db.session_factory(resolved)
        run_store = repositories.SqlRunStore(factory)
        ticket_store = repositories.SqlTicketStore(factory)
        tool_call_store = repositories.SqlToolCallStore(factory)
        approval_store = repositories.SqlApprovalStore(factory)
    except (ImportError, ModuleNotFoundError):
        # The persistence package itself is missing or cannot import its
        # driver. Naming the database would be a guess; this is a deployment
        # that cannot supply the layer at all.
        raise WorkerConfigError(
            "the persistence layer could not be imported",
            how_to_fix="install the project's dependencies (see pyproject.toml)",
        ) from None
    except _DATABASE_BUILD_ERRORS as exc:
        raise WorkerConfigError(
            f"the worker could not build its stores from DATABASE_URL "
            f"({resolved.database_url})",
            how_to_fix=(
                "set DATABASE_URL to a reachable database and run "
                "'alembic upgrade head' so the schema exists (see .env.example)"
            ),
        ) from exc

    stack = build_worker_retrieval(session_factory=factory)

    def _recorder_factory(run_id: UUID) -> TraceRecorder:
        return TraceRecorder(run_id=run_id, session_factory=factory)

    return _Worker(
        worker_id=resolve_worker_id(resolved),
        session_factory=factory,
        run_store=run_store,
        ticket_store=ticket_store,
        tool_call_store=tool_call_store,
        approval_store=approval_store,
        provider=build_worker_provider(resolved),
        # The gateway builds its three MCP servers in-process on first use. It
        # does not take the ``MCP_*_COMMAND`` settings: those name the stdio
        # subprocesses a Compose deployment runs, and the in-process path is the
        # one documented as the default (``tools/mcp_gateway.py``).
        gateway=MCPToolGateway(),
        # The linear executor, not LangGraph: deterministic, milliseconds-fast and
        # dependency-free. ``run_loop`` accepts the orchestrator but does not
        # dispatch through it today (``agents/runtime.py``) -- the port is what
        # keeps the two interchangeable, and the contract test is what proves it.
        orchestrator=LinearOrchestrator(),
        recorder_factory=_recorder_factory,
        retrieval=stack.retrieval,
        citation_store=build_citation_store(factory),
        poll_interval=resolved.worker_poll_interval,
    )


def _install_signal_handlers(stop: asyncio.Event) -> dict[signal.Signals, object]:
    """Ask for a graceful stop on ``SIGINT``/``SIGTERM``; return the old handlers.

    ``loop.add_signal_handler`` would be the event-loop-native route, but it is
    Unix-only and raises ``NotImplementedError`` on Windows -- where this
    project's development and CI machines run. ``signal.signal`` works on both,
    and the handler only calls ``Event.set()``, which is enough: CPython runs the
    handler on the main thread between bytecodes, so the flag is set while the
    loop is blocked in its poll sleep.

    Two consequences, both intended:

    - **A signal is a request, not a crash.** With a handler installed ``SIGINT``
      no longer raises ``KeyboardInterrupt``, so Ctrl-C stops the worker with
      exit code 0 instead of a traceback.
    - **The stop is a poll boundary, never a mid-step kill.** The flag is read
      once per iteration, so a run already claimed is driven to its own terminal
      or parked status first.

    The previous handlers are returned so :func:`main` can put them back -- which
    matters when ``main()`` is called in-process (a test), where leaving ours
    installed would swallow the next Ctrl-C for the whole process.
    """
    previous: dict[signal.Signals, object] = {}

    def _handler(_signum: int, _frame: FrameType | None) -> None:
        # The signal number is part of the handler signature
        # (``signal.signal`` passes it) and is unused: every handled signal means
        # the same thing here, "stop the loop".
        stop.set()

    for signum in _HANDLED_SIGNALS:
        try:
            previous[signum] = signal.signal(signum, _handler)
        except ValueError:  # pragma: no cover - not the main thread
            continue
    return previous


def _restore_signal_handlers(previous: dict[signal.Signals, object]) -> None:
    """Put back what :func:`_install_signal_handlers` replaced."""
    for signum, handler in previous.items():
        with contextlib.suppress(TypeError, ValueError):
            signal.signal(signum, handler)  # type: ignore[arg-type]


async def _run(worker: _Worker) -> None:
    """Boot, poll until asked to stop, then return.

    The boot sweep runs before the first claim: runs left in a claim-and-work
    status by a *previous* process are marked ``FAILED('interrupted')``, and
    ``WAITING_APPROVAL`` and ``EXECUTING`` are deliberately spared -- the second
    because a run can be ``EXECUTING`` only because a human approved and the
    worker died before finishing, and failing it would discard that decision
    (``docs/architecture.md`` §5).

    Shutdown does **not** sweep. ``_install_signal_handlers`` sets a flag that
    ends the loop at the next iteration, so a run in flight is driven to its own
    terminal or parked status and its status is already durable when the process
    exits. A run interrupted *between* two gate decisions is left in a
    claim-and-work status, which is exactly what the next boot's sweep is for --
    and trying to be clever here is how a shutdown would fail an ``EXECUTING``
    run whose refund a human already approved. Mid-step resume remains Phase 2;
    what is guaranteed here is only that nothing is *lost*.

    An exception out of ``poll_forever`` is re-raised rather than swallowed, so a
    worker whose loop broke dies loudly with a non-zero exit instead of exiting 0
    having stopped working.
    """
    stop = asyncio.Event()
    previous = _install_signal_handlers(stop)

    marked = mark_interrupted_on_boot(worker.session_factory)
    print(
        f"opspilot-worker[{worker.worker_id}]: booted; "
        f"marked {marked} interrupted run(s) failed; "
        f"polling every {worker.poll_interval}s",
        flush=True,
    )

    poll = asyncio.ensure_future(
        poll_forever(
            worker_id=worker.worker_id,
            run_store=worker.run_store,
            ticket_store=worker.ticket_store,
            tool_call_store=worker.tool_call_store,
            approval_store=worker.approval_store,
            provider=worker.provider,
            gateway=worker.gateway,
            orchestrator=worker.orchestrator,
            poll_interval=worker.poll_interval,
            recorder_factory=worker.recorder_factory,
            retrieval=worker.retrieval,  # type: ignore[arg-type]
            citation_store=worker.citation_store,
            retrieval_min_score=get_settings().retrieval_min_score,
        )
    )
    stopping = asyncio.ensure_future(stop.wait())
    try:
        done, pending = await asyncio.wait(
            {poll, stopping}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if poll in done and not poll.cancelled():
            # The loop ended on its own, which it only does by raising.
            poll.result()
    finally:
        _restore_signal_handlers(previous)
        print(f"opspilot-worker[{worker.worker_id}]: stopped", flush=True)


def main() -> None:
    """Run the worker until interrupted.

    Exits non-zero -- without starting the loop -- when the deployment cannot
    supply the collaborators the loop needs, naming what to set. Exits 0 on a
    clean shutdown.
    """
    try:
        worker = build_worker()
    except WorkerConfigError as exc:
        print(f"opspilot-worker: refusing to start -- {exc}", file=__import__("sys").stderr)
        raise SystemExit(1) from exc
    asyncio.run(_run(worker))


if __name__ == "__main__":
    main()
