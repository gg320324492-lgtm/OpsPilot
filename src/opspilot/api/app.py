"""FastAPI application factory.

Responsibility: build the app -- mount routers, install the auth dependency,
register the exception handlers, and wire concrete adapters to the ports. This
is the composition root for the API process.

Layer: ``api``. The only place in the API that chooses adapters -- the routers
receive ports.

Startup behaviour: the app refuses to start when ``OPSPILOT_OPERATOR_TOKEN`` is
unset, rather than defaulting to open. A default-open auth is worse than no auth
because it looks like auth.

Browser access: the app installs a ``CORSMiddleware`` whose allowed origins come
from ``OPSPILOT_CORS_ORIGINS`` and are never wildcarded, because the M7 dashboard
runs on a different port from the API and a browser blocks the response without
it. CORS decides which pages the browser will *hand a response to*; it is not an
authorisation mechanism and does not replace the bearer token. See
``_cors_options`` for why each middleware flag is set the way it is and
``docs/api-contract.md`` §8 for the deployment-facing statement.

Adapters are injected, not imported. The routers depend on the ``RunStore`` /
``TicketStore`` / ``ApprovalStore`` *ports*; the concrete SQL implementations are
attached to ``app.state`` here, and an in-memory fake replaces them in tests by
calling :func:`create_app` with the same keyword arguments. That is what keeps
the API testable before -- and independently of -- the persistence layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, TypedDict, cast

from fastapi import Depends, FastAPI
from starlette.middleware.cors import CORSMiddleware

from opspilot.api.auth import ensure_operator_token_configured
from opspilot.api.errors import ERROR_RESPONSES, register_exception_handlers
from opspilot.api.routers import approvals, health, knowledge, runs, tickets
from opspilot.settings import Settings, WildcardCorsOrigin, get_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

    from opspilot.adapters.wiring import RetrievalStack
    from opspilot.ports.stores import ApprovalStore, RunStore, TicketStore


class _CorsOptions(TypedDict):
    """The ``CORSMiddleware`` keyword arguments this app installs.

    A ``TypedDict`` rather than a plain ``dict[str, object]`` so the values stay
    typed through ``add_middleware(**options)``: under ``--strict`` a bare dict
    degrades every keyword to ``object`` and the call no longer type-checks. The
    middleware's signature is the schema this has to keep matching, so a new field
    added there surfaces here as a type error rather than as a silently dropped
    argument.
    """

    allow_origins: list[str]
    allow_credentials: bool
    allow_methods: list[str]
    allow_headers: list[str]
    max_age: int

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
            When omitted, a real one is built from the same session factory as
            the stores (the deployment path); when the caller also injected
            stores and no database was built, the fallback checker reports
            ``unconfigured`` and the route answers 503 rather than a false
            ``ok``. Tests may inject a checker to pin a specific response.

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

    app.add_middleware(CORSMiddleware, **_cors_options(settings))

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
    # guarded routers. Every guarded router shares ``ERROR_RESPONSES`` so the
    # contract's "one error shape, everywhere" (``docs/api-contract.md`` §6) is
    # something a generated client can read rather than only something the
    # humans are bound by. The health routes are exempt: they are token-free by
    # design and declare their own 503.
    app.include_router(health.router)
    app.include_router(tickets.router, responses=_router_responses())
    app.include_router(runs.router, responses=_router_responses())
    app.include_router(approvals.router, responses=_router_responses())
    app.include_router(knowledge.router, responses=_router_responses())

    register_exception_handlers(app)
    return app


def _router_responses() -> dict[int | str, dict[str, object]]:
    """A fresh copy of the shared error declarations for one router.

    Copied per router because FastAPI mutates the mapping it is handed while
    merging it into the document; sharing one dict across four routers would let
    the first merge leave its state behind for the next three.
    """
    return {code: dict(entry) for code, entry in ERROR_RESPONSES.items()}


def _cors_options(settings: Settings) -> _CorsOptions:
    """Build the ``CORSMiddleware`` keyword arguments for this deployment.

    Every value here is deliberate; the reasons are load-bearing rather than
    conventional, so they are written out next to the values.

    ``allow_origins`` comes from ``OPSPILOT_CORS_ORIGINS`` and is never
    wildcarded. The field validator in :mod:`opspilot.settings` refuses to
    construct settings containing ``*``, and the second check below re-asserts it
    at the point of use, so a future edit that builds the list by another route
    still fails loudly instead of widening the API.

    ``allow_credentials=True`` because the dashboard sends
    ``Authorization: Bearer`` -- that is a credential, and without this flag the
    browser strips the response before the page sees it. This combination is the
    reason the wildcard is refused rather than merely discouraged: Starlette
    would refuse to attach ``Access-Control-Allow-Credentials`` to a wildcard
    response anyway, so a ``*`` here would mean a *broken* dashboard sitting
    next to an unbounded read path.

    ``allow_methods`` is the documented write set of ``docs/api-contract.md``
    §1, not ``*``. The contract is GET, POST, and preflight-OPTIONS; a method the
    contract does not define has no route to reach, so naming them is free
    precision.

    ``allow_headers`` must include ``Authorization``: it is on the browser's
    preflight list, and omitting it is the single most common reason a CORS
    config that "looks right" still fails every request. ``Content-Type`` is
    there because every write in the contract posts JSON.

    ``max_age`` is 600s so a developer editing the dashboard does not send a
    preflight on every hot-reload, without being long enough that revoking an
    origin takes ten minutes to take effect.

    An empty ``allow_origins`` installs the middleware with an empty allowlist,
    which refuses every cross-origin request. That is the intended behaviour for
    "no dashboard": the app still builds and every same-origin and non-browser
    client is unaffected.
    """
    origins = settings.cors_origins
    if any(origin == "*" for origin in origins):
        # Unreachable while the settings validator holds. Present because this is
        # the function that would otherwise be the one place to widen the API,
        # and a guard that only exists in a sibling module is a guard that is one
        # refactor away from not existing.
        raise WildcardCorsOrigin
    return {
        "allow_origins": list(origins),
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Authorization", "Content-Type"],
        "max_age": 600,
    }


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

    The readiness checker is built from the *same* session factory as the stores
    when the caller injected neither, so the probe and the stores share one
    engine and one connection pool -- a probe against a different engine could
    pass while the stores' engine is down. A caller that injects stores (the
    tests do, with in-memory fakes and no database) is not probing a database it
    does not have, and the checker falls back to reporting that honestly rather
    than to a false ``ok``.
    """
    session_factory: sessionmaker[Session] | None = None
    if run_store is None or ticket_store is None or approval_store is None:
        session_factory, sql_run, sql_ticket, sql_approval = _build_sql_stores()
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
    app.state.readiness_check = readiness_check or _build_readiness_check(session_factory)


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


def _build_sql_stores() -> tuple[sessionmaker[Session], RunStore, TicketStore, ApprovalStore]:
    """Build the concrete SQL stores, importing persistence lazily.

    Returns the session factory alongside the stores so the caller can hand the
    *same* factory to the readiness checker: one engine, one pool.

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
        factory = db.session_factory(settings)
        run_store = repositories.SqlRunStore(factory)
        ticket_store = repositories.SqlTicketStore(factory)
        approval_store = repositories.SqlApprovalStore(factory)
        return (
            factory,
            cast("RunStore", run_store),
            cast("TicketStore", ticket_store),
            cast("ApprovalStore", approval_store),
        )
    except TypeError as exc:  # pragma: no cover - persistence not finished
        raise _PersistenceUnavailable("construct") from exc


def _build_readiness_check(
    session_factory: sessionmaker[Session] | None,
) -> Callable[[], Awaitable[dict[str, str]]]:
    """Build the ``/ready`` checker, degrading honestly when persistence is absent.

    Two cases, and the difference matters:

    - The deployment path built its own SQL stores, so a ``session_factory`` is
      present and a real checker is returned -- the fix this module needed.
    - The caller injected stores (the tests do, with in-memory fakes and no
      database URL wired), so there is no engine to probe. Rather than report
      ``ok`` for a database that was never checked -- the original defect -- the
      returned checker reports ``database: unconfigured`` for every call, which
      the route turns into a ``503``.

    That second case is the deliberate answer to "what does an unbound checker
    mean": it is no longer possible to mistake the absence of a checker for
    health. An app with injected stores and no database genuinely cannot certify
    readiness, so it declines to.
    """
    if session_factory is not None:
        from opspilot.adapters.persistence import readiness

        return readiness.build_readiness_check(get_settings(), session_factory=session_factory)

    async def _unconfigured() -> dict[str, str]:
        return {"database": "unconfigured", "migrations": "unconfigured"}

    return _unconfigured


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
