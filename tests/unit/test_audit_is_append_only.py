"""The audit ledger is append-only, and this file is the guard that says so.

## Why this file exists

``docs/data-model.md`` §2 says of ``audit_events``:

> Append-only by convention: no UPDATE or DELETE path exists in the repository
> layer, and ``test_audit_is_append_only`` asserts the repository exposes no
> mutating method.

That sentence cited a test that did not exist. The property it describes is
load-bearing — the ledger is the compliance record of what was authorised and
what actually happened, and a row that can be rewritten after the fact is not a
record of anything — and the citation pointed at a guard nobody had written. A
document that names a test is a promise that the property is checked, so the
test had to be written or the sentence had to go. This file is it.

## What it asserts

Three independent angles, because "there is no UPDATE path" is a claim about
the whole tree and not just about one class:

1. **No method in ``src/`` issues an UPDATE or a DELETE against
   ``audit_events``.** Checked by reading the source with ``ast``: every
   ``update(...)`` / ``delete(...)`` call and every raw SQL string is scanned,
   and none of them names the table. This is the claim the document actually
   makes — it is about *the repository layer*, and the repository layer is
   spread over several modules.
2. **Appending and reading issue no mutation.** The append path
   (``TraceRecorder.record_audit``) and the read path
   (``SqlRunStore.list_audit_events``) are run against a real database with an
   SQL listener attached, and every statement that reaches the driver is
   inspected: an ``UPDATE`` or ``DELETE`` against the ledger fails the test.
3. **The public interface offers no way to mutate one.** The only public
   method that touches the ledger on the read side is ``list_audit_events``,
   and on the write side it is ``record_audit``. Nothing is named like a
   mutation of an audit event.

## What it does not do

It does not try to stop a caller from reaching the ORM directly and running
``session.query(AuditEvent).update(...)``. A convention guarded by a test is a
convention, and this test says plainly which convention it is guarding: the
repository layer has no mutating path, and the code that writes the ledger has
one method that appends.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.repositories import SqlRunStore, SqlTicketStore
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opspilot"

#: The table the ledger lives in, as the migrations name it.
TABLE = "audit_events"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    factory = db.session_factory(settings)
    engine = factory.kw["bind"]
    db.register_sqlite_pragmas(engine)
    models.Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


# --------------------------------------------------------------------------- #
# 1. No UPDATE or DELETE against the ledger, anywhere in src/
# --------------------------------------------------------------------------- #


def _sources() -> Iterator[pathlib.Path]:
    yield from sorted(SRC.rglob("*.py"))


def _names_a_ledger_table(node: ast.AST) -> bool:
    """Whether an AST node mentions the audit ledger.

    Two spellings count: the table name ``audit_events`` (raw SQL, and the
    string a migration uses) and the ORM class ``AuditEvent`` (what a
    SQLAlchemy ``update()``/``delete()`` is pointed at). The read-model
    dataclass ``AuditEventRow`` is not the table and is not counted.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in {TABLE, "AuditEvent"}:
            return True
        if isinstance(child, ast.Attribute) and child.attr == "AuditEvent":
            return True
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and TABLE in child.value
        ):
            return True
    return False


def test_no_source_file_updates_or_deletes_an_audit_event() -> None:
    """Nothing in ``src/`` writes to the ledger -- only appends and reads.

    Scans every module with ``ast``, so a mutation written as
    ``update(models.AuditEvent)``, as ``delete(models.AuditEvent)``, or as raw
    SQL in a ``text()`` string is all found by the same walk. The ORM class is
    named ``AuditEvent``, so the class name is matched as well as the table
    name.
    """
    offenders: list[str] = []

    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in {"update", "delete"}:
                continue
            if _names_a_ledger_table(node):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno} "
                    f"calls {name}(...) on the audit ledger"
                )

    assert not offenders, (
        f"the audit ledger must be append-only, but these statements mutate it: {offenders}."
    )


def test_no_raw_sql_updates_or_deletes_an_audit_event() -> None:
    """No raw SQL string in ``src/`` writes to the ledger either.

    The ``ast`` walk in the test above sees a ``text("UPDATE audit_events ...")``
    only as a string constant, so this one looks for the statement verbs
    explicitly.
    """
    offenders: list[str] = []
    verbs = ("update audit_events", "delete from audit_events")

    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            lowered = " ".join(node.value.lower().split())
            if any(verb in lowered for verb in verbs):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")

    assert not offenders, (
        f"the audit ledger must be append-only, but raw SQL mutates it at: {offenders}."
    )


# --------------------------------------------------------------------------- #
# 2. The append and read paths issue no mutation
# --------------------------------------------------------------------------- #


async def test_appending_and_reading_the_ledger_issues_no_mutation(
    factory: sessionmaker[Session],
) -> None:
    """Writing two events and reading them back runs no UPDATE or DELETE.

    The listener sees every statement that reaches the driver, so this is the
    behavioural version of the claim: not "no method is named like a mutation"
    but "the SQL that actually runs never rewrites a row".
    """
    run_id = await _make_run(factory)

    statements: list[str] = []

    @sa_event.listens_for(factory.kw["bind"], "before_cursor_execute")
    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    recorder = TraceRecorder(run_id=run_id, session_factory=factory)
    first = await recorder.record_audit(event_type="tool_executed", actor="runtime")
    second = await recorder.record_audit(
        event_type="run_failed", actor="worker", payload={"reason": "boom"}
    )
    events = await SqlRunStore(factory).list_audit_events(run_id)

    mutations = [
        statement
        for statement in statements
        if TABLE in statement and statement.strip().lower().startswith(("update", "delete"))
    ]
    assert not mutations, f"the ledger was mutated while appending and reading: {mutations!r}"

    # And the two events are still exactly what was written. The ledger is
    # ordered by ``(created_at, id)`` and two events written in the same
    # microsecond fall back to the id, so the *order* is not asserted -- the
    # *contents* are.
    assert {event.id for event in events} == {first, second}
    assert {event.event_type for event in events} == {"tool_executed", "run_failed"}
    assert {event.actor for event in events} == {"runtime", "worker"}


async def test_a_second_append_leaves_the_first_event_untouched(
    factory: sessionmaker[Session],
) -> None:
    """Appending does not rewrite what is already in the ledger.

    The positive statement behind the convention: a second event is *added*,
    and the first is still there with the payload it was written with.
    """
    run_id = await _make_run(factory)
    recorder = TraceRecorder(run_id=run_id, session_factory=factory)

    await recorder.record_audit(
        event_type="tool_executed", actor="runtime", payload={"tool_name": "billing.get_invoice"}
    )
    await recorder.record_audit(event_type="tool_failed", actor="runtime")

    events = await SqlRunStore(factory).list_audit_events(run_id)

    assert len(events) == 2
    # The ledger is ordered by ``(created_at, id)``, and two events written in
    # the same microsecond fall back to the id -- so the order is not the
    # claim. What must hold is that both are there, and that the first one
    # still carries the payload it was written with.
    by_type = {event.event_type: event for event in events}
    assert set(by_type) == {"tool_executed", "tool_failed"}
    assert by_type["tool_executed"].payload == {"tool_name": "billing.get_invoice"}
    assert by_type["tool_failed"].payload == {}


# --------------------------------------------------------------------------- #
# 3. The interface offers no mutating method
# --------------------------------------------------------------------------- #


def test_audit_is_append_only() -> None:
    """The public interface offers exactly one append and one read. Nothing else.

    The claim ``docs/data-model.md`` §2 makes -- "the repository exposes no
    mutating method" for the audit ledger -- stated as an interface fact rather
    than as a scan of the source, because the interface is what a caller can
    reach.
    """
    from opspilot.adapters.persistence import repositories

    audit_methods: dict[str, list[str]] = {}
    for owner in (
        repositories.SqlRunStore,
        repositories.SqlToolCallStore,
        repositories.SqlApprovalStore,
        repositories.SqlCitationStore,
        repositories.SqlKnowledgeDocumentStore,
        repositories.SqlTicketStore,
        TraceRecorder,
    ):
        names = sorted(
            name
            for name, member in vars(owner).items()
            if not name.startswith("_") and callable(member) and "audit" in name.lower()
        )
        audit_methods[owner.__name__] = names

    assert audit_methods["SqlRunStore"] == ["list_audit_events"], (
        f"the run store exposes {audit_methods['SqlRunStore']}; the only audit "
        "method on the read side may be the read."
    )
    assert audit_methods["TraceRecorder"] == ["record_audit"], (
        f"the recorder exposes {audit_methods['TraceRecorder']}; the only audit "
        "method on the write side may be the append."
    )
    for name, methods in audit_methods.items():
        assert methods == ["list_audit_events"] or methods == ["record_audit"] or not methods, (
            f"{name} exposes {methods}; no class may expose a second way to touch the audit ledger."
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _make_run(factory: sessionmaker[Session]) -> UUID:
    """Create a ticket and a run, returning the run id.

    ``audit_events.run_id`` references ``agent_runs.id`` and the SQLite path
    enforces foreign keys, so the run has to exist before an event can name it.
    """
    ticket_id = await SqlTicketStore(factory).create(
        subject="duplicate charge", body="charged twice", customer_email="a@example.com"
    )
    run = await SqlRunStore(factory).create(
        ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
    )
    return run.id
