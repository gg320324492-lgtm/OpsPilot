"""A tool's gate-1 schema must accept what the contract says the tool takes.

## Why this file exists

`test_mcp_contract_names.py` compares the registered tool *names* against the
contract, after the M2 servers registered bare names while the contract and
`TOOL_REGISTRY` used prefixed ones. That test covers names. Nothing covered
*arguments*, and the same class of drift was sitting in `crm.get_customer`.

The contract (`docs/mcp-contracts.md` §1) says, in its own words:

    { "customer_email": "billing@acme.example" }
    // or
    { "customer_id": "CUS-1001" }

    Exactly one of `customer_email` / `customer_id` must be given.

The MCP server implements exactly that (`mcp_servers/crm/server.py::get_customer`
takes both, optional). But gate 1 validated the call against
`CustomerLookupArgs`, which declared **only** `customer_id` with
`extra="forbid"`. So a model that proposed `{"customer_email": ...}` -- the
contract's own first example, and the shape the live recording of 2026-10-15
shows the model choosing -- was rejected at gate 1 before the tool ran.

That is the identical defect `TransactionListArgs` records having survived four
milestones: "no argument set satisfied both sides". The lesson that entry draws
is the one this test enforces -- the two sides must be compared, not each
tested against itself.

## What is asserted

For every tool the contract documents with an `// input` block, each parameter
name appearing in that block is accepted by the tool's registered gate-1
schema. A parameter the schema forbids is a call the contract promises will
work and the code refuses.

The comparison reads the **document**, not the schema, so it cannot pass by
checking the schema against itself.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from pydantic import ValidationError

from opspilot.domain.tools import TOOL_ARGUMENT_SCHEMAS, InvalidArgumentsError, validate_tool_call

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "mcp-contracts.md"

#: A placeholder that satisfies any string field, so validation is reached.
_SAMPLE = "x"


def _input_blocks() -> dict[str, list[str]]:
    """Every documented tool's input parameter names, from the contract.

    The document gives each tool an ``### `server.tool` `` heading followed by a
    `jsonc` block whose ``// input`` section lists the accepted shapes; a tool
    may show more than one alternative (``crm.get_customer`` shows an email
    form and an id form). Each alternative's keys are collected separately and
    flattened, because a schema that accepts either form is the requirement --
    not one schema that accepts the union of both at once, which would let a
    caller pass both.
    """
    text = CONTRACT.read_text(encoding="utf-8")
    # Split on tool headings so each block is attributed to the right tool.
    sections = re.split(r"^###\s+`((?:crm|billing|issues)\.[a-z_]+)`", text, flags=re.MULTILINE)
    names: dict[str, list[str]] = {}
    # sections[0] is the preamble; then name, body, name, body, ...
    for i in range(1, len(sections), 2):
        tool = sections[i]
        body = sections[i + 1]
        # The first jsonc fence after the heading holds the example exchange.
        fence = re.search(r"```jsonc\n(.*?)```", body, re.DOTALL)
        if fence is None:
            names[tool] = []
            continue
        example = fence.group(1)

        # The document marks the boundary between the input and the output in
        # two different ways, and both are in use: a `// output` comment
        # (`crm.get_customer`), and a `→` arrow (`billing.get_invoice`). Taking
        # only the first would read a tool's *response* fields as parameters it
        # accepts, and report a schema as too narrow when it is exact -- which is
        # a false positive that hides the real drift this file is for.
        boundary = len(example)
        for marker in ("// output", "→"):
            found = example.find(marker)
            if found != -1:
                boundary = min(boundary, found)
        input_side = example[:boundary]

        parameters: list[str] = []
        for line in input_side.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or not stripped.startswith("{"):
                continue
            # Only the object's own top-level keys are parameters. A nested
            # field (an object inside an argument) is reached through its
            # parent, so a line that continues an object started earlier is
            # skipped by requiring the line to open a brace.
            for key in re.findall(r'"([a-z_]+)"\s*:', stripped):
                if key not in parameters:
                    parameters.append(key)
        names[tool] = parameters
    return names


@pytest.fixture(scope="module")
def documented_arguments() -> dict[str, list[str]]:
    """The contract's parameter names per tool, guarded against parsing nothing."""
    parsed = _input_blocks()
    assert parsed, f"no tool sections were parsed out of {CONTRACT}; the pattern no longer matches"
    with_inputs = {tool: params for tool, params in parsed.items() if params}
    assert with_inputs, (
        f"no `// input` parameters were parsed out of {CONTRACT}. A comparison "
        f"against nothing passes for every tool, so this is a failure, not a "
        f"vacuous success."
    )
    return with_inputs


@pytest.mark.parametrize("tool", sorted(_input_blocks()))
def test_the_gate_accepts_every_parameter_the_contract_documents(
    tool: str, documented_arguments: dict[str, list[str]]
) -> None:
    """Each documented parameter name is accepted by the tool's gate-1 schema."""
    parameters = documented_arguments.get(tool)
    if not parameters:
        pytest.skip(f"{tool} documents no input parameters")
    schema = TOOL_ARGUMENT_SCHEMAS.get(tool)
    assert schema is not None, (
        f"the contract documents `{tool}` but it has no gate-1 schema in "
        f"TOOL_ARGUMENTS, so no parameter can be validated against it"
    )

    forbidden = []
    for name in parameters:
        try:
            schema.model_validate({name: _SAMPLE})
        except ValidationError:
            forbidden.append(name)

    assert forbidden == [], (
        f"the contract documents these parameters for `{tool}` but its gate-1 "
        f"schema ({schema.__name__}) rejects them: {forbidden}. A model that "
        f"proposes a contract-valid call is refused before the tool runs -- the "
        f"drift `TransactionListArgs` records having survived four milestones. "
        f"Schema declares: {sorted(schema.model_fields)}"
    )


def test_get_customer_accepts_both_identifiers_the_contract_offers() -> None:
    """`crm.get_customer` takes an email or an id -- the contract says so twice.

    Stated separately from the parametrised test because this is the defect the
    file was written for, and a regression should name itself rather than
    surface as "one of N parameters failed". The server accepts both
    (`mcp_servers/crm/server.py::get_customer`); gate 1 must agree.
    """
    schema = TOOL_ARGUMENT_SCHEMAS["crm.get_customer"]

    schema.model_validate({"customer_email": "billing@acme.example"})
    schema.model_validate({"customer_id": "CUS-1001"})


def test_get_customer_still_refuses_both_identifiers_at_once() -> None:
    """The contract's "exactly one" is enforced, not just documented.

    Widening the schema to accept `customer_email` must not widen it into
    accepting both: the contract says guessing which identifier was meant "is
    how a wrong customer gets refunded", so a call carrying both is refused at
    the gate rather than resolved by the tool.
    """
    schema = TOOL_ARGUMENT_SCHEMAS["crm.get_customer"]

    with pytest.raises(ValidationError):
        schema.model_validate({"customer_email": "billing@acme.example", "customer_id": "CUS-1001"})


def test_the_live_recording_replays_through_the_current_gate() -> None:
    """The recorded calls, replayed against the fixed schemas.

    `docs/evidence/live-run-2026-10-15.json` holds what a real model actually
    proposed. Replaying it is the only way to see what this file's fix did to
    real traffic rather than to a constructed case — and, just as importantly,
    what it did not.

    Three outcomes, and the third is the one worth keeping:

    * ``issues.create`` now **passes**. Before the fix it was refused at gate 1
      because the schema declared ``body`` where the contract and the server
      take ``description``/``priority``/``labels``. This is the fix working on
      real input.
    * ``crm.get_customer`` with ``email`` **still fails**, and must.
    * ``done`` carries no tool, so there is nothing to validate.

    The second point is the reason this test exists rather than a looser one
    that just asserts "the recording validates". The recorded call sent
    ``email``; the contract spells it ``customer_email``. That argument is wrong
    under either schema, so a fix that made it pass would be a fix that stopped
    enforcing the contract — which is the opposite of what
    ``docs/limitations.md`` §8 records. The 0.471 argument-validity figure is
    unchanged by the schema repair, and this test is where that stays true.
    """
    recorded = json.loads(
        (REPO_ROOT / "docs" / "evidence" / "live-run-2026-10-15.json").read_text(encoding="utf-8")
    )
    proposals = [
        call["response"]["value"]
        for call in recorded
        if call["method"] == "choose_tool" and call["response"]["value"].get("tool_name")
    ]
    assert proposals, "the recording holds no tool proposals; the evidence file changed shape"

    outcomes: dict[str, bool] = {}
    for proposal in proposals:
        name = proposal["tool_name"]
        try:
            validate_tool_call(name, dict(proposal["arguments"]))
        except InvalidArgumentsError:
            outcomes[f"{name} {proposal['arguments']}"] = False
        else:
            outcomes[f"{name} {proposal['arguments']}"] = True

    # `issues.create` with the contract's own fields is accepted now.
    created = [ok for key, ok in outcomes.items() if key.startswith("issues.create")]
    assert created, "the recording's issues.create proposal was not replayed"
    assert all(created), (
        "the recording's issues.create call is refused again; the schema repair "
        f"was reverted. Outcomes: {outcomes}"
    )

    # `email` is still not `customer_email`, and must still be refused.
    customer = [ok for key, ok in outcomes.items() if key.startswith("crm.get_customer")]
    assert customer, "the recording's crm.get_customer proposal was not replayed"
    assert not any(customer), (
        "a crm.get_customer call carrying `email` now validates. The contract "
        "spells it `customer_email`; accepting the wrong name would make this "
        "schema stop enforcing the contract it exists to enforce. "
        f"Outcomes: {outcomes}"
    )
