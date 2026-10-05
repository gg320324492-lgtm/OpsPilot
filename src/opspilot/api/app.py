"""FastAPI application factory.

Responsibility: build the app -- mount routers, install the auth dependency,
register the exception handlers, and wire concrete adapters to the ports. This
is the composition root for the API process.

Layer: ``api``. The only place in the API that chooses adapters -- the routers
receive ports.

Startup behaviour: the app refuses to start when ``OPSPILOT_OPERATOR_TOKEN`` is
unset, rather than defaulting to open. A default-open auth is worse than no auth
because it looks like auth.

Adapters are injected, not imported. The routers depend on the ``RunStore`` /
``TicketStore`` / ``ApprovalStore`` *ports*; the concrete SQL implementations are
attached to ``app.state`` here, and an in-memory fake replaces them in tests by
calling :func:`create_app` with the same keyword arguments. That is what keeps
the API testable before -- and independently of -- the persistence layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from fastapi import Depends, FastAPI

from opspilot.api.auth import ensure_operator_token_configured
from opspilot.api.errors import register_exception_handlers
from opspilot.api.routers import approvals, health, knowledge, runs, tickets
from opspilot.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from opspilot.adapters.wiring import RetrievalStack
    from opspilot.ports.stores import ApprovalStore, RunStore, TicketStore

# The name the OpenAPI document is tagged with; also what a probe sees.
_TITLE = "OpsPilot API"
_DESCRIPTION = (
    "A reliable AI operations agent for B2B support and billing. The model "
    "proposes, deterministic code decides, tools execute, humans approve risky "
    "actions, everything is traced."
)


def create_app(
    *,
    run_store: RunStore | None = None,
    ticket_store: TicketStore | None = None,
    approval_store: ApprovalStore | None = None,
    knowledge_store: object | None = None,
    reindex_runner: Callable[[Path], Awaitable[object]] | None = None,
    readiness_check: Callable[[], Awaitable[dict[str, str]]] | None = None,
) -> FastAPI:
    """Construct and return the configured FastAPI application.

    Args:
        run_store: The ``RunStore`` port implementation. When ``None``, the
            concrete SQL adapter is built from settings -- deferred to a local
            import so importing this module does not require the persistence
            package to exist (which is what lets the persistence layer be written
            in parallel).
        ticket_store: The ``TicketStore`` port implementation.
        approval_store: The ``ApprovalStore`` port implementation.
        knowledge_store: Optional store exposing ``list_documents`` for
            ``GET /api/knowledge``.
        reindex_runner: Optional callable performing a reindex; the ingestion
            adapter supplies it in a full deployment.
        readiness_check: Optional async callable returning the ``/ready`` checks.

    Returns:
        The app, with routers mounted, handlers registered and stores bound.

    Raises:
        MissingOperatorToken: If ``OPSPILOT_OPERATOR_TOKEN`` is unset or empty.
    """
    settings = get_settings()

    # Start-up token check. Deliberately here, not in the dependency: refusing to
    # construct the app means an unconfigured deployment cannot serve a single
    # request, rather than answering 401 to every one of them.
    ensure_operator_token_configured(settings)

    app = FastAPI(
        title=_TITLE,
        description=_DESCRIPTION,
        version="0.1.0",
        dependencies=[Depends(_noop_dependency)],
    )

    _bind_stores(
        app,
        run_store=run_store,
        ticket_store=ticket_store,
        approval_store=approval_store,
        knowledge_store=knowledge_store,
        reindex_runner=reindex_runner,
        readiness_check=readiness_check,
    )

    # Health first, so its two token-exempt routes are registered before the
    # guarded routers.
    app.include_router(health.router)
    app.include_router(tickets.router)
    app.include_router(runs.router)
    app.include_router(approvals.router)
    app.include_router(knowledge.router)

    register_exception_handlers(app)
    return app


def _bind_stores(
    app: FastAPI,
    *,
    run_store: RunStore | None,
    ticket_store: TicketStore | None,
    approval_store: ApprovalStore | None,
    knowledge_store: object | None,
    reindex_runner: Callable[[Path], Awaitable[object]] | None,
    readiness_check: Callable[[], Awaitable[dict[str, str]]] | None,
) -> None:
    """Attach the store implementations to ``app.state`` for the dependencies.

    When a store is not supplied, the SQL adapter is imported lazily and built
    from settings. The lazy import is the point: this module can be imported and
    the app built with fakes even while ``adapters.persistence`` is incomplete.
    """
    if run_store is None or ticket_store is None or approval_store is None:
        sql_run, sql_ticket, sql_approval = _build_sql_stores()
        run_store = run_store or sql_run
        ticket_store = ticket_store or sql_ticket
        approval_store = approval_store or sql_approval

    # The knowledge surfaces are wired from the same settings the SQL stores use,
    # but only when the caller did not inject fakes. `GET /api/knowledge` then
    # lists real documents and `POST /api/knowledge/reindex` ingests them; a
    # deployment that cannot build the stack (no database yet) leaves both
    # unbound, and the router degrades to the documented empty/zero responses
    # rather than taking the API down (``knowledge.py``).
    if knowledge_store is None or reindex_runner is None:
        stack = _try_build_retrieval_stack()
        if stack is not None:
            knowledge_store = knowledge_store or stack.knowledge_store
            reindex_runner = reindex_runner or stack.reindex_runner

    app.state.run_store = run_store
    app.state.ticket_store = ticket_store
    app.state.approval_store = approval_store
    app.state.knowledge_store = knowledge_store
    app.state.reindex_runner = reindex_runner
    app.state.readiness_check = readiness_check


def _try_build_retrieval_stack() -> RetrievalStack | None:
    """Build the retrieval stack, or ``None`` when persistence is unavailable.

    The stack needs a session factory, which needs the database layer; while that
    layer is incomplete (or the process is starting without a database) the app
    must still construct, so the failure is caught and reported as "not wired"
    rather than raised. The router already handles an unbound store, so a
    partially-configured deployment degrades instead of refusing to start.
    """
    try:
        from opspilot.adapters.wiring import build_retrieval_stack
    except ImportError:  # pragma: no cover - wiring fault while a layer is absent
        return None
    try:
        return build_retrieval_stack(get_settings())
    except (ImportError, TypeError):  # pragma: no cover - persistence not finished
        return None


def _build_sql_stores() -> tuple[RunStore, TicketStore, ApprovalStore]:
    """Build the concrete SQL stores, importing persistence lazily.

    Raises:
        _PersistenceUnavailable: If the persistence package cannot supply the
            stores yet, with a message naming the missing piece, so a deployment
            failure is self-explaining rather than an ``ImportError`` from three
            frames down.
    """
    try:
        from opspilot.adapters.persistence import db, repositories
    except ImportError as exc:  # pragma: no cover - wiring fault
        raise _PersistenceUnavailable("import") from exc

    settings = get_settings()
    try:
        sessionmaker = db.session_factory(settings)
        run_store = repositories.SqlRunStore(sessionmaker)
        ticket_store = repositories.SqlTicketStore(sessionmaker)
        approval_store = repositories.SqlApprovalStore(sessionmaker)
        return (
            cast("RunStore", run_store),
            cast("TicketStore", ticket_store),
            cast("ApprovalStore", approval_store),
        )
    except TypeError as exc:  # pragma: no cover - persistence not finished
        raise _PersistenceUnavailable("construct") from exc


class _PersistenceUnavailable(RuntimeError):
    """The concrete SQL stores could not be built.

    Raised only when the app is started *without* injected stores and the
    persistence package cannot supply them. The message says what to do instead
    (inject fakes), so a wiring failure is self-explaining rather than an
    ``ImportError`` from three frames down.
    """

    _REASONS: ClassVar[dict[str, str]] = {
        "import": "opspilot.adapters.persistence is not importable",
        "construct": "the repositories do not accept a session factory",
    }

    def __init__(self, kind: str) -> None:
        reason = self._REASONS.get(kind, kind)
        super().__init__(
            f"the API needs store implementations, but opspilot.adapters.persistence "
            f"could not supply them ({reason}); pass run_store/ticket_store/approval_store "
            "to create_app (tests do this with fakes)"
        )


async def _noop_dependency() -> None:
    """An app-wide dependency placeholder.

    Routes declare their own auth dependency (or are the health routes, which are
    exempt); this exists so the app has a single place a future cross-cutting
    dependency can be attached without editing every router.
    """
