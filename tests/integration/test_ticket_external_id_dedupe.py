"""``POST /api/tickets`` must answer a duplicate ``external_id`` with 409, not 500.

## Why this file exists

``docs/api-contract.md`` §9 says, in the table of write-endpoint behaviour:

> | `POST /api/tickets` | Always creates. A client wanting dedupe sends
> `external_id`; a unique violation returns `409 ticket_exists` with the existing
> ticket. |

The code did the opposite. ``tickets.external_id`` is genuinely unique -- the
model declares it and ``0001_initial_schema`` writes
``uq_tickets_external_id`` -- so the second post died where the contract says it
should be answered:

    IntegrityError: UNIQUE constraint failed: tickets.external_id

and, because the router had nothing to catch it, the envelope handler returned
``500 internal_error: An internal error occurred.`` with a ``trace_id`` in
``details``. Meanwhile ``errors.py`` carried ``"ticket_exists": 409`` in its code
table with nothing in the codebase raising it, and the dashboard's ticket form
already rendered ``409 ticket_exists: ...`` for a status the API could not
produce. That is the ``docs/limitations.md`` §8 defect class -- a capability a
document advertises that no code performs -- and the P2-C3 audit saw it live on
the running system.

## Why not against the fakes

``tests/integration/test_api_tickets.py`` covers this endpoint, and covers it
properly, but with ``tests/integration/fakes.py`` -- dicts in memory, keyed by
uuid, with no index to violate. ``FakeTicketStore.create`` appends whatever it is
handed, so a fake-backed assertion of the 409 would be asserting the test
double's behaviour. The uniqueness lives in the schema, so the store that has it
is the store these tests use: the same argument, in the same shape, as
``test_list_endpoints_against_the_real_store.py`` and ``test_api_audit.py``.

## Both dialects, because the promise is unconditional

§9 says a unique violation returns 409 with no dialect qualifier, and ADR-0004
records that the default suite runs on SQLite while production is Postgres 16 +
pgvector. The two drivers report the same violation in shapes that share no
substring -- SQLite names the *column* (``UNIQUE constraint failed:
tickets.external_id``) because it has no named constraints to report, while
psycopg reports the constraint *name* structurally on ``diag.constraint_name``.
``_is_external_id_violation`` in ``repositories.py`` reads both, and
``test_the_dialect_matrix_recognises_only_the_tickets_external_id_violation``
below pins every arm of it -- including the arms that must answer ``False``.

The two postgres-marked tests then take the other half on a real server: that the
migrated schema carries the constraint under the name the code looks for, and
that the same duplicate really is a 409 there. They skip cleanly where no
Postgres is reachable (ADR-0004); CI's ``verify`` job names both by node id and
asserts they PASSED.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models, repositories
from opspilot.adapters.persistence.models import Base
from opspilot.api.app import create_app
from opspilot.domain.errors import TicketExists
from opspilot.settings import Settings, get_settings

# Imported, not re-declared. ``test_migration_schema.py``'s fixture carries the
# details that are easy to get wrong -- it clears the process-wide
# ``get_settings`` cache (without which ``alembic upgrade head`` migrates the
# default SQLite database while the assertions read an empty Postgres, silently),
# and it drops and recreates the public schema. A second copy would be a second
# thing to keep true about how a test database is prepared.
#
# The import is what makes the fixture *findable* from this module; the tests
# below take it as a parameter. Deleting it looks right -- ruff calls it unused --
# and the postgres tests then error with a missing fixture.
from tests.integration.test_migration_schema import migrated_engine  # noqa: F401

_TOKEN = "ticket-dedupe-test-token"  # noqa: S105 -- a fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

#: The payload the dashboard's form posts (``web/src/app/tickets/ticket-form.tsx``).
#: ``external_id`` is merged in by the tests that want one, because the form omits
#: the key entirely when the field is left blank -- its default -- so the
#: no-``external_id`` test below posts exactly what a blank form posts.
_FORM_TICKET: dict[str, str] = {
    "subject": "Charged twice for invoice INV-2026-384",
    "body": "We were charged twice for invoice INV-2026-384.",
    "customer_email": "billing@acme.example",
}


def _app(factory: sessionmaker[Session]) -> FastAPI:
    """The real app bound to the real SQL stores over the given factory.

    Every store is passed explicitly so nothing reaches for the process's own
    ``DATABASE_URL`` -- a factory built from settings here would answer from a
    different database from the one these tests write to, and the duplicate post
    would succeed for a reason that has nothing to do with the behaviour under
    test.
    """
    return create_app(
        run_store=repositories.SqlRunStore(factory),
        ticket_store=repositories.SqlTicketStore(factory),
        approval_store=repositories.SqlApprovalStore(factory),
    )


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004).

    ``StaticPool`` (inside ``db.create_engine``) is what makes this one database
    rather than one per connection, so the second ``POST`` in each test below
    sees the row the first one committed -- which is the whole premise.
    """
    session_factory = db.session_factory(Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"))
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


@pytest.fixture
def client(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A ``TestClient`` over the real SQLite stores."""
    with _client_over(factory, monkeypatch) as test_client:
        yield test_client


@contextmanager
def _client_over(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Build the app with the test token in place, clearing the settings cache."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("MODEL_NAME", "fake-model")
    get_settings.cache_clear()
    try:
        with TestClient(_app(factory)) as test_client:
            yield test_client
    finally:
        get_settings.cache_clear()


def _external_ids(factory: sessionmaker[Session]) -> list[str | None]:
    """Every stored ``external_id``, read through the session rather than the API.

    Counting rows here rather than through ``GET /api/tickets`` keeps these
    assertions about what was *persisted*: a handler that returned 409 while
    quietly writing a second row would still pass a response-only test.
    """
    with db.session_scope(factory) as session:
        return list(session.execute(select(models.Ticket.external_id)).scalars().all())


def _run_count(factory: sessionmaker[Session]) -> int:
    """How many runs the store holds, read through the session rather than the API."""
    with db.session_scope(factory) as session:
        return session.execute(select(func.count()).select_from(models.AgentRun)).scalar_one()


# ---------------------------------------------------------------------------
# The contract's four promises about a duplicate external_id
# ---------------------------------------------------------------------------


def test_the_first_post_still_creates_a_ticket_and_a_run(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """§9 opens with "Always creates": the first post is 201 and lands.

    This is the assertion that keeps the fix from becoming a read-or-reject path.
    The 409 below must be produced by a *constraint firing*, not by the handler
    deciding in advance that the key looks taken -- so the first post has to
    create, and the test says so before any duplicate exists.
    """
    response = client.post(
        "/api/tickets", headers=_AUTH, json={**_FORM_TICKET, "external_id": "ZD-55102"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ticket"]["external_id"] == "ZD-55102"
    assert body["run"]["status"] == "received"
    assert _external_ids(factory) == ["ZD-55102"]


def test_a_duplicate_external_id_is_409_ticket_exists_with_the_existing_ticket(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """The contract's promise, end to end: 409, the right code, and the ticket.

    §9 says the violation "returns 409 ticket_exists **with the existing
    ticket**". Carrying the ticket is not decoration: a client that has lost its
    own copy of the id -- a retry after a dropped response, which is exactly how
    duplicates happen -- needs to be told which ticket it already has, not only
    that it made one.
    """
    first = client.post(
        "/api/tickets", headers=_AUTH, json={**_FORM_TICKET, "external_id": "ZD-55102"}
    )
    assert first.status_code == 201, first.text
    created = first.json()["ticket"]

    response = client.post(
        "/api/tickets",
        headers=_AUTH,
        json={**_FORM_TICKET, "external_id": "ZD-55102", "body": "A different body."},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "ticket_exists"
    assert error["details"]["external_id"] == "ZD-55102"

    # Compared against ``GET /api/tickets/{id}``, not against the 201 body. The
    # 409's ticket is re-read from the database, so it is the *stored* row the
    # client is being told about -- and that is the same representation every
    # other ticket read serves. (The rejected request's body deliberately
    # differs, so a response echoing what was *asked for* fails here.)
    fetched = client.get(f"/api/tickets/{created['id']}", headers=_AUTH).json()["ticket"]
    assert error["details"]["ticket"] == fetched
    assert error["details"]["ticket"]["id"] == created["id"]
    assert error["details"]["ticket"]["subject"] == created["subject"]
    assert error["details"]["ticket"]["body"] == created["body"]

    # ...and nothing was created on the way to the 409: one ticket, not two.
    assert _external_ids(factory) == ["ZD-55102"]


def test_the_409_body_is_the_single_error_envelope(client: TestClient) -> None:
    """§6's shape, with nothing else in it.

    §6 promises one shape everywhere, so this asserts the three fields it names
    *and* that FastAPI's default ``{"detail": ...}`` did not leak -- the same
    check ``test_api_tickets.py`` makes for ``schema_invalid``. A 409 that
    changed shape would be a new way for the dashboard's ``describeFailure`` to
    fall through to "answered with a body this page could not read".
    """
    payload = {**_FORM_TICKET, "external_id": "ZD-55102"}
    assert client.post("/api/tickets", headers=_AUTH, json=payload).status_code == 201

    response = client.post("/api/tickets", headers=_AUTH, json=payload)

    body = response.json()
    assert "detail" not in body, "FastAPI's default error shape leaked through"
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    # The id travels as a string, not a raw UUID object: ``details`` is
    # ``dict[str, object]`` and is serialized for the wire, so anything left as a
    # Python object here would be a promise the JSON cannot keep.
    assert isinstance(body["error"]["details"]["ticket_id"], str)


def test_two_different_external_ids_both_create(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """The constraint is not over-broad: distinct keys never collide.

    A guard against the cheap version of this fix -- treating any second post as
    a duplicate, or matching ``external_id`` loosely enough that two real
    customers' references collide.
    """
    first = client.post(
        "/api/tickets", headers=_AUTH, json={**_FORM_TICKET, "external_id": "ZD-55102"}
    )
    second = client.post(
        "/api/tickets", headers=_AUTH, json={**_FORM_TICKET, "external_id": "ZD-55103"}
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["ticket"]["id"] != second.json()["ticket"]["id"]
    assert sorted(str(v) for v in _external_ids(factory)) == ["ZD-55102", "ZD-55103"]


def test_two_posts_without_an_external_id_both_create(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """``external_id`` is nullable and two absences are not a collision.

    SQL's ``UNIQUE`` does not hold for ``NULL`` on either dialect -- Postgres
    would need ``NULLS NOT DISTINCT`` for that, which this schema does not use --
    so this is a property of the schema as written rather than of the code path.
    It is worth pinning because the fix reads ``external_id`` off the request to
    decide whether a violation can be a duplicate at all, and the dashboard's
    commonest post is the one with no ``external_id`` to read.
    """
    assert "external_id" not in _FORM_TICKET, "this test posts what a blank form posts"

    first = client.post("/api/tickets", headers=_AUTH, json=_FORM_TICKET)
    second = client.post("/api/tickets", headers=_AUTH, json=_FORM_TICKET)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["ticket"]["external_id"] is None
    assert _external_ids(factory) == [None, None]


def test_a_duplicate_creates_no_second_run(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """A rejected post creates no run, so the queue never doubles up.

    The rejected insert is not the only thing that must not happen. A run
    enqueued for a ticket that was never created would leave the worker claiming a
    ``RECEIVED`` run whose ``ticket_id`` points nowhere and investigating a ticket
    that does not exist -- a worse outcome than the 500 this replaces, and one no
    response-only assertion would see.
    """
    client.post("/api/tickets", headers=_AUTH, json={**_FORM_TICKET, "external_id": "ZD-55102"})
    assert _run_count(factory) == 1

    duplicate = client.post(
        "/api/tickets", headers=_AUTH, json={**_FORM_TICKET, "external_id": "ZD-55102"}
    )

    assert duplicate.status_code == 409, duplicate.text
    assert _run_count(factory) == 1, "the rejected post enqueued a second run"


# ---------------------------------------------------------------------------
# Where the 409 is decided: the store, and only for this constraint
# ---------------------------------------------------------------------------


async def test_the_store_raises_the_named_condition_not_the_driver_error(
    factory: sessionmaker[Session],
) -> None:
    """``SqlTicketStore.create`` raises ``TicketExists``, not ``IntegrityError``.

    The named condition is what the router branches on. Had the store let the
    driver's exception through, the router would have to know about SQLite and
    Postgres, and ``docs/architecture.md``'s layering would put SQLAlchemy inside
    the ``api`` layer.
    """
    store = repositories.SqlTicketStore(factory)
    await store.create(
        subject="s", body="b", customer_email="c@example.com", external_id="ZD-55102"
    )

    with pytest.raises(TicketExists) as caught:
        await store.create(
            subject="s", body="b", customer_email="c@example.com", external_id="ZD-55102"
        )

    assert caught.value.external_id == "ZD-55102"
    # It carries the id of the ticket already holding the key, which is where the
    # 409's `details.ticket` is read back from.
    assert isinstance(caught.value.ticket_id, UUID)
    # The driver's error is chained, not swallowed: the log still has it, and a
    # reader of the traceback can see which constraint actually fired.
    assert isinstance(caught.value.__cause__, IntegrityError)


class _PostgresError(Exception):
    """A stand-in for ``psycopg.errors.UniqueViolation``, which carries a ``diag``."""

    def __init__(self, message: str, constraint_name: str | None) -> None:
        super().__init__(message)
        self.diag: Any = type("_Diag", (), {"constraint_name": constraint_name})()


def _violation(orig: Exception) -> IntegrityError:
    """Wrap a driver's error the way SQLAlchemy does."""
    return IntegrityError("INSERT ...", {}, orig)


#: ``(label, orig, recognised)``. The ``False`` rows are the point of the table:
# they are every other integrity failure this store can hit, and each must keep
# answering 500 rather than being reported as a duplicate ``external_id``.
_DIALECT_MATRIX: tuple[tuple[str, Exception, bool], ...] = (
    ("sqlite, this constraint", Exception("UNIQUE constraint failed: tickets.external_id"), True),
    ("sqlite, a different column", Exception("UNIQUE constraint failed: knowledge.source"), False),
    (
        "sqlite, not a unique failure",
        Exception("NOT NULL constraint failed: tickets.subject"),
        False,
    ),
    (
        "postgres, the migrated constraint name",
        _PostgresError("dup key", "uq_tickets_external_id"),
        True,
    ),
    (
        "postgres, the name create_all derives",
        _PostgresError("dup key", "tickets_external_id_key"),
        True,
    ),
    (
        "postgres, a different constraint",
        _PostgresError("dup key", "uq_tool_calls_idempotency"),
        False,
    ),
    ("postgres, a foreign key", _PostgresError("violates fk", "fk_agent_runs_ticket_id"), False),
    (
        "postgres, message only, this constraint",
        Exception(
            'duplicate key value violates unique constraint "uq_tickets_external_id"\n'
            "DETAIL:  Key (external_id)=(ZD-55102) already exists."
        ),
        True,
    ),
    (
        "postgres, message only, another constraint",
        Exception(
            'duplicate key value violates unique constraint "uq_tool_calls_idempotency"\n'
            "DETAIL:  Key (idempotency_key)=(refund:X:TX-88219) already exists."
        ),
        False,
    ),
    ("neither dialect", Exception("connection reset by peer"), False),
)


@pytest.mark.parametrize(
    ("orig", "recognised"),
    [pytest.param(row[1], row[2], id=row[0]) for row in _DIALECT_MATRIX],
)
def test_the_dialect_matrix_recognises_only_the_tickets_external_id_violation(
    orig: Exception, recognised: bool
) -> None:
    """Every shape the two drivers produce, classified.

    Both ``True`` rows the production dialects produce are here -- Postgres'
    structural ``diag`` and SQLite's column-naming message -- and so is the
    ``False`` row that matters most: another constraint's violation, reported
    through the same Postgres path. This is the test that keeps a future widening
    visible, because without it "catch ``IntegrityError`` and return 409" is a
    change nobody notices was too broad.
    """
    assert repositories._is_external_id_violation(_violation(orig)) is recognised


# ---------------------------------------------------------------------------
# The other dialect: the migrated Postgres, where §9's promise is unconditional
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_client(
    # A fixture parameter shares the name of the import that makes it findable,
    # which ruff reads as a redefinition. It is the pytest idiom.
    migrated_engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A ``TestClient`` over the real stores bound to the migrated Postgres.

    Deliberately not the SQLite ``client``: this is a different dialect, a
    different driver and a different ``IntegrityError`` shape. Everything the
    handler does above the store is identical, which is the point -- the 409 is
    partly the dialect's business and this is the dialect ADR-0004 exists because
    SQLite cannot speak for.
    """
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False, class_=Session)
    with _client_over(factory, monkeypatch) as test_client:
        yield test_client


@pytest.mark.postgres
def test_the_migrated_postgres_enforces_the_constraint_under_the_expected_name(
    migrated_engine: Engine,  # noqa: F811 -- fixture parameter, see postgres_client
) -> None:
    """The migration writes a named unique constraint on ``tickets.external_id``.

    Read from ``pg_constraint`` rather than assumed, because this is the half of
    ADR-0004 SQLite cannot check: the code looks the violation up **by name**, so
    a schema whose index carried some other name would reject every duplicate on
    the deployment database and answer 500 -- the exact defect, moved.

    **This test drops and recreates the schema** through the shared
    ``migrated_engine`` fixture. Point ``OPSPILOT_DATABASE_URL`` at a disposable
    database only; that is what CI's service container is.
    """
    from sqlalchemy import text

    with migrated_engine.connect() as connection:
        names = (
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'tickets'::regclass AND contype = 'u'"
                )
            )
            .scalars()
            .all()
        )

    assert "uq_tickets_external_id" in names, f"migrated tickets constraints were {names}"


@pytest.mark.postgres
def test_a_duplicate_external_id_on_postgres_is_409(postgres_client: TestClient) -> None:
    """The same 409 over a real pgvector Postgres, through psycopg.

    This is the test that would have caught the defect on the database a
    deployment actually runs. It skips cleanly where no Postgres is reachable
    (ADR-0004), and CI's ``verify`` job names it by node id and asserts PASSED,
    so a skip there cannot read as coverage.
    """
    payload = {**_FORM_TICKET, "external_id": "ZD-55102"}
    first = postgres_client.post("/api/tickets", headers=_AUTH, json=payload)
    assert first.status_code == 201, first.text
    created = first.json()["ticket"]

    response = postgres_client.post("/api/tickets", headers=_AUTH, json=payload)

    assert response.status_code == 409, response.text
    body = response.json()["error"]
    assert body["code"] == "ticket_exists"
    # Compared against the endpoint that renders this same ticket, so the
    # assertion is about *which* ticket came back and not about how one dialect
    # formats a timestamp.
    fetched = postgres_client.get(f"/api/tickets/{created['id']}", headers=_AUTH).json()["ticket"]
    assert body["details"]["ticket"] == fetched
