"""The registry is the authority on which tools exist; the gateway is not.

``domain/tools.py``'s ``TOOL_REGISTRY`` is the single source of truth for what a
tool *is* -- its name, its permission, its server. A gateway advertises what it
*can execute*; the two answers are not the same thing, and only one of them is
authority.

The gap this file closes: ``tests/security/conftest.py``'s ``SpyGateway.list_tools()``
returns ``[]`` on purpose (the registry, not the gateway, is authoritative), and
``tests/integration/test_composite_gateway.py`` covers the listing side. Nothing
drove ``_gate_and_execute`` with a gateway that *advertises* a tool the registry
does not have, and asserted that the proposal for it was still refused.

Why it matters. If gate 2 consulted the gateway instead of the registry, then any
tool a gateway offered would become callable -- with whatever permission that
gateway chose. The gateway is exactly the kind of component the model's influence
surface might reach (it is configured by the deployment, it talks to MCP servers
whose tool lists are dynamic), so a tool's permission must never be inherited from
one. An unregistered tool has no permission *at all*, and the only safe answer to
"may I call this?" is no.

What this file deliberately does **not** assert:

* that the gateway's advertised list matches the registry -- that is
  ``tests/integration/test_mcp_contract_names.py``'s job;
* that a registered tool executes -- the gates are covered by
  ``test_approval_bypass.py``;
* that the *server* refuses an unknown name -- the gateway's own
  ``unknown_tool`` refusal is covered by ``test_composite_gateway.py``. What is
  asserted here is that the refusal happens **before** the gateway is ever asked
  to dispatch.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import models
from opspilot.agents.runtime import GATE_REGISTRY, _gate_and_execute
from opspilot.agents.schemas import ProposedAction
from opspilot.domain.tools import TOOL_REGISTRY, Permission, ToolCallStatus, ToolSpec
from opspilot.tracing.recorder import TraceRecorder

from .conftest import SpyGateway, make_context, make_run

# A name a gateway might plausibly offer and the registry does not have. It is
# deliberately *not* in ``TOOL_REGISTRY``: that is the premise of the test, and it
# is asserted below so the test cannot pass for the wrong reason (a rename that
# registered it would make the premise false).
_ADVERTISED = "billing.wire_transfer"


class _AdvertisingGateway(SpyGateway):
    """A spy whose gateway *advertises* a tool the static registry does not have.

    It inherits the spy's recording behaviour -- every dispatch is recorded -- and
    overrides only the listing. The advertised spec claims a high-risk permission,
    which is the dangerous case: a gateway that could introduce a high-risk write
    by advertising it is exactly the authority confusion this test rules out.
    """

    async def list_tools(self) -> list[ToolSpec]:
        """Advertise one tool the registry has never heard of."""
        return [
            ToolSpec(
                name=_ADVERTISED,
                permission=Permission.HIGH_RISK_WRITE,
                server="billing",
                description="Advertised by the gateway, absent from the registry.",
            )
        ]


def _tool_calls(factory: sessionmaker[Session]) -> list[models.ToolCall]:
    """Every tool call row, ordered by creation."""
    with factory() as session:
        return list(
            session.execute(select(models.ToolCall).order_by(models.ToolCall.created_at)).scalars()
        )


async def test_a_gateway_advertised_tool_the_registry_lacks_is_refused_at_gate_2(
    factory: sessionmaker[Session],
) -> None:
    """A tool the gateway offers but the registry does not have is refused.

    The gateway here advertises ``billing.wire_transfer`` as a high-risk write.
    The registry has never heard of the name, so gate 2 -- the *registry* lookup --
    must refuse the proposal, and it must refuse it before the gateway is asked to
    dispatch anything. The tool call row is recorded at the least-privileged level
    (``read``), the way every refusal before gate 3 is recorded, because no
    permission was ever looked up for a tool that does not exist.

    The two premises are asserted first -- the gateway really does advertise the
    name, and the registry really does not have it -- so the refusal cannot be
    green for the wrong reason (a typo in the name, or a registry that quietly
    grew the tool).
    """
    gateway = _AdvertisingGateway()
    stores = await make_run(factory)
    ctx = make_context(stores)

    # Premise 1: the gateway offers the name.
    advertised = {spec.name for spec in await gateway.list_tools()}
    assert _ADVERTISED in advertised
    # Premise 2: the registry does not have it.
    assert _ADVERTISED not in TOOL_REGISTRY

    record = await _gate_and_execute(
        ctx,
        ProposedAction(tool_name=_ADVERTISED, arguments={"amount": "1000000"}),
        gateway=gateway,
        run_store=stores.run_store,
        tool_call_store=stores.tool_call_store,
        approval_store=stores.approval_store,
        recorder=TraceRecorder(run_id=stores.run.id, session_factory=stores.factory),
    )

    assert record.status == ToolCallStatus.REJECTED.value

    # Nothing was dispatched: the gateway was never asked to execute the name it
    # advertised, only to refuse it.
    assert gateway.dispatch_count == 0

    rows = _tool_calls(factory)
    assert len(rows) == 1
    call = rows[0]
    assert call.tool_name == _ADVERTISED
    assert call.status == ToolCallStatus.REJECTED.value
    assert call.rejection_reason == GATE_REGISTRY
    assert call.error == "unknown_tool"

    # Recorded at the least-privileged level: no permission was looked up for a
    # tool the registry does not have, so none was written.
    assert call.permission == Permission.READ.value

    # And no approval row: the proposal never got as far as a human.
    assert await stores.approval_store.get_for_tool_call(call.id) is None


def test_approval_is_decided_by_the_permission_and_by_nothing_else() -> None:
    """One source for the answer "does this need a human".

    ``ToolSpec.requires_approval`` used to be a field of its own, set to
    ``True`` on the refund, and read by **nobody**: gate 5 decides from
    ``permission is HIGH_RISK_WRITE`` (``agents/runtime.py``). Two answers to
    one question is one too many, and the field was the dangerous one of the
    two -- a ``SAFE_WRITE`` tool registered with it set would have *looked*
    approval-gated and run automatically.

    It is now derived from the permission, so the two cannot disagree, and the
    model refuses the field as an input, so "it cannot be set separately" is
    enforced rather than merely intended. The direction of the derivation is
    the point: a high-risk tool needs approval whether or not anybody
    remembered to say so.
    """
    for name, spec in TOOL_REGISTRY.items():
        assert spec.requires_approval is (spec.permission is Permission.HIGH_RISK_WRITE), name

    # Asked for the way a caller would ask -- through ``model_validate``, so
    # the field reaches the model and the model refuses it. Passing it as a
    # keyword argument would be a type error, which is the point: the field is
    # not part of the model's vocabulary any more.
    with pytest.raises(ValidationError):
        ToolSpec.model_validate(
            {
                "name": "billing.issue_refund",
                "permission": "safe_write",
                "server": "billing",
                "requires_approval": True,
            }
        )
