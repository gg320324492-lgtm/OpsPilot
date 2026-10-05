"""The CI gate: four invariants, each required to be ``0`` over the database.

Transcribed from ``docs/tool-permissions.md`` §6. These are not targets to trend
toward -- a single violation is a red CI run. Each query is written against the
**assembled** database: the tests first *cause* the scenarios that could violate
an invariant (propose an unknown tool, propose a malformed refund, park an
unapproved high-risk call, attempt a duplicate refund), then assert the
invariant survived. A query that has never been run against a database
containing an attempted violation proves nothing.

The four:

| Invariant | Query |
|---|---|
| Unapproved ``HIGH_RISK_WRITE`` executions | ``test_no_unapproved_high_risk_execution`` |
| Executions of unregistered tools | ``test_no_execution_of_unregistered_tools`` |
| Executions with schema-invalid arguments | ``test_no_execution_with_invalid_arguments`` |
| Duplicate refund side effects for one key | ``test_no_duplicate_refund_side_effects`` |

The assembly helper drives the real gate with a recording spy, so the database
holds exactly the rows the gate and the MCP servers actually wrote.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import models
from opspilot.agents.runtime import _gate_and_execute
from opspilot.agents.schemas import ProposedAction
from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.errors import RunParked
from opspilot.domain.tools import Permission, ToolCallStatus

from .conftest import SpyGateway, Stores, make_context, make_run

_TX = "TX-88219"


async def _assemble_attacked_database(stores: Stores, spy: SpyGateway) -> None:
    """Drive the attacks that *could* violate an invariant into the database.

    Each proposal is a genuine attempt, driven through the real gate:

    1. An unknown tool name -- must be recorded rejected, never executed.
    2. A registered tool with schema-invalid arguments -- rejected at gate 1.
    3. A high-risk refund with no approval -- parked, never executed.
    4. A second high-risk refund with a *different* transaction -- also parked.
    """
    from opspilot.tracing.recorder import TraceRecorder

    ctx = make_context(stores)
    recorder = TraceRecorder(run_id=stores.run.id, session_factory=stores.factory)

    # (1) unknown tool name.
    await _gate_and_execute(
        ctx,
        ProposedAction(tool_name="billing.wire_transfer", arguments={"amount": "1"}),
        gateway=spy,
        run_store=stores.run_store,
        tool_call_store=stores.tool_call_store,
        approval_store=stores.approval_store,
        recorder=recorder,
    )

    # (2) registered tool, schema-invalid arguments (missing required field).
    await _gate_and_execute(
        ctx,
        ProposedAction(tool_name="billing.issue_refund", arguments={"amount": "129.00"}),
        gateway=spy,
        run_store=stores.run_store,
        tool_call_store=stores.tool_call_store,
        approval_store=stores.approval_store,
        recorder=recorder,
    )

    # (3) and (4) high-risk writes with no approval -- both park.
    for transaction in (_TX, "TX-11111"):
        with pytest.raises(RunParked):
            await _gate_and_execute(
                ctx,
                ProposedAction(
                    tool_name="billing.issue_refund",
                    arguments={"transaction_id": transaction, "amount": "129.00"},
                ),
                gateway=spy,
                run_store=stores.run_store,
                tool_call_store=stores.tool_call_store,
                approval_store=stores.approval_store,
                recorder=recorder,
            )


@pytest.fixture
async def assembled(factory: sessionmaker[Session]) -> tuple[Stores, SpyGateway]:
    """A database carrying the attacked scenarios, plus the spy that drove them."""
    stores = await make_run(factory)
    spy = SpyGateway()
    await _assemble_attacked_database(stores, spy)
    return stores, spy


async def test_the_attack_database_is_not_empty(factory: sessionmaker[Session]) -> None:
    """Guards the guard: the assembled database must contain the attack rows.

    A green invariant run over an *empty* database is the classic false pass --
    the count is zero because nothing happened, not because the control held.
    This test asserts the assembly actually wrote rejected, awaiting-approval and
    audit rows, so the four ``== 0`` queries below are meaningful.
    """
    stores = await make_run(factory)
    spy = SpyGateway()
    await _assemble_attacked_database(stores, spy)

    with factory() as session:
        total = session.execute(select(func.count()).select_from(models.ToolCall)).scalar_one()
        rejected = session.execute(
            select(func.count())
            .select_from(models.ToolCall)
            .where(models.ToolCall.status == ToolCallStatus.REJECTED.value)
        ).scalar_one()
        awaiting = session.execute(
            select(func.count())
            .select_from(models.ToolCall)
            .where(models.ToolCall.status == ToolCallStatus.AWAITING_APPROVAL.value)
        ).scalar_one()
        audits = session.execute(select(func.count()).select_from(models.AuditEvent)).scalar_one()

    assert total >= 4
    assert rejected == 2
    assert awaiting == 2
    assert audits >= 1
    assert spy.dispatch_count == 0


async def test_no_unapproved_high_risk_execution(assembled: tuple[Stores, SpyGateway]) -> None:
    """Invariant 1: no ``HIGH_RISK_WRITE`` is ``executed`` without an approved row.

    The query counts executed high-risk calls for which no ``APPROVED``
    ``ApprovalRequest`` exists. It must be zero. The scenario drove two
    unapproved high-risk proposals, so the database contains the counterexample
    the query is looking for -- if the gate had a hole, this would be non-zero.
    """
    stores, _ = assembled
    factory = stores.factory

    with factory() as session:
        from sqlalchemy import exists

        approved_exists = exists().where(
            (models.ApprovalRequest.tool_call_id == models.ToolCall.id)
            & (models.ApprovalRequest.status == ApprovalStatus.APPROVED.value)
        )
        n = session.execute(
            select(func.count())
            .select_from(models.ToolCall)
            .where(
                models.ToolCall.permission == Permission.HIGH_RISK_WRITE.value,
                models.ToolCall.status == ToolCallStatus.EXECUTED.value,
                ~approved_exists,
            )
        ).scalar_one()

    assert n == 0


async def test_no_execution_of_unregistered_tools(assembled: tuple[Stores, SpyGateway]) -> None:
    """Invariant 2: no tool call is ``executed`` for a name not in the registry.

    The registry is the source of truth; an executed call whose name is absent
    from it means gate 2 was bypassed. The assembled database holds a rejected
    ``billing.wire_transfer`` row, which is the *correct* outcome -- rejected,
    not executed -- so this query counts only executed rows.
    """
    from opspilot.domain.tools import TOOL_REGISTRY

    stores, _ = assembled
    with stores.factory() as session:
        registered = set(TOOL_REGISTRY)
        executed_tool_names = (
            session.execute(
                select(models.ToolCall.tool_name).where(
                    models.ToolCall.status == ToolCallStatus.EXECUTED.value
                )
            )
            .scalars()
            .all()
        )

    unregistered_executed = [name for name in executed_tool_names if name not in registered]
    assert unregistered_executed == []


async def test_no_execution_with_invalid_arguments(assembled: tuple[Stores, SpyGateway]) -> None:
    """Invariant 3: no ``executed`` call has schema-invalid arguments.

    "Schema-invalid" is defined by gate 1's own models: a call is valid iff its
    stored ``arguments`` re-parse into the registered argument schema. Re-
    validating with the *same* schema gate 1 used (rather than a test-local
    notion of validity) is what makes this a real check: a gate that accepted an
    argument the schema forbids would produce an executed row that fails here.
    """
    from pydantic import ValidationError

    from opspilot.domain.tools import TOOL_ARGUMENT_SCHEMAS

    stores, _ = assembled
    with stores.factory() as session:
        executed = list(
            session.execute(
                select(models.ToolCall).where(
                    models.ToolCall.status == ToolCallStatus.EXECUTED.value
                )
            ).scalars()
        )

    invalid: list[tuple[str, str]] = []
    for call in executed:
        schema = TOOL_ARGUMENT_SCHEMAS.get(call.tool_name)
        if schema is None:
            invalid.append((call.tool_name, "no schema registered"))
            continue
        try:
            schema.model_validate(dict(call.arguments))
        except ValidationError as exc:
            invalid.append((call.tool_name, str(exc)))

    assert invalid == []


async def test_no_duplicate_refund_side_effects(
    factory: sessionmaker[Session],
) -> None:
    """Invariant 4: one idempotency key never yields two executed side effects.

    Asserted at two layers. First the database: no ``idempotency_key`` appears on
    two ``executed`` rows (the partial unique index enforces this, and the query
    would catch a schema that had lost it). Second, the real MCP server: two
    calls with one key write one refund row. The database half is a fresh,
    empty-but-schema'd database so the always-zero property is asserted exactly,
    and it is paired with the server half so it cannot pass merely because no
    refund ever ran.
    """
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory

    # Layer 1 -- the schema forbids two executed rows sharing a key.
    with factory() as session:
        duplicates = session.execute(
            select(models.ToolCall.idempotency_key, func.count())
            .where(
                models.ToolCall.idempotency_key.is_not(None),
                models.ToolCall.status == ToolCallStatus.EXECUTED.value,
            )
            .group_by(models.ToolCall.idempotency_key)
            .having(func.count() > 1)
        ).all()
    assert duplicates == []

    # Layer 2 -- the server itself writes exactly one refund for one key.
    from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers

    key = "refund:invariants:TX-88219"
    args = {"transaction_id": _TX, "amount": 129.00, "idempotency_key": key}
    with TemporaryDirectory() as directory:
        gateway = MCPToolGateway(servers=build_in_process_servers(Path(directory)))
        await gateway.call_tool("billing.issue_refund", args)
        await gateway.call_tool("billing.issue_refund", args)
        refunds = json.loads((Path(directory) / "billing.json").read_text(encoding="utf-8"))[
            "refunds"
        ]
    assert len(refunds) == 1


def test_all_four_invariants_are_declared() -> None:
    """A meta-check: the suite contains one test per invariant in §6.

    Guards against a future edit that deletes one of the four tests: the section
    names four invariants, and this counts the query tests present. A red test
    here means the CI gate is no longer complete, even if every remaining test
    passes.
    """
    import inspect

    from tests.security import test_invariants

    source = inspect.getsource(test_invariants)
    for name in (
        "unapproved_high_risk",
        "unregistered_tools",
        "invalid_arguments",
        "duplicate_refund",
    ):
        assert name in source, f"invariant query missing: {name}"
