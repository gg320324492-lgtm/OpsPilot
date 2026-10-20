"""A tool's gate-1 schema and the tool itself must accept the same arguments.

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

## What is asserted, in both directions

**Contract → schema.** For every tool the contract documents with an `// input`
block, each parameter name appearing in that block is accepted by the tool's
registered gate-1 schema. A parameter the schema forbids is a call the contract
promises will work and the code refuses.

**Schema → the real tool.** Every field the gate-1 schema puts on the wire is a
parameter the MCP server actually takes. This is the direction that was missing,
and the missing direction is why a defect got through three times.

The third of its kind was `RefundArgs.currency`: the model could propose a
currency the server has no parameter for, and the server -- whose generated
schema has no `additionalProperties: false` -- silently ignored it. The
currency in the proposal was therefore never executed, yet it reached the
approver inside the `risk_explanation`, the one string they are told to trust,
and inside the arguments snapshot they approve against.

No existing guard could see it, and the reasons are worth naming:

* the *behavioural* guard (`tests/unit/test_permissions.py::
  test_every_gate_1_schema_is_accepted_by_the_real_tool`) dispatches the
  schema's **required** fields only, and a server that ignores unknown fields
  cannot answer "did you accept this field?" -- it answers "did the call
  work?", which it did;
* the guard in this file read the **document**, so a field the schema declares
  and the server does not take was invisible to it;
* `tests/unit/test_permissions.py::test_gate_1_schema_and_server_agree_on_
  required_arguments` compares the server's **required** set against the
  schema's, so a field the schema declares and the server does not take -- an
  optional one -- was invisible to that too.

So the comparison now reads the server's **own generated JSON Schema** and
compares the full parameter set against the schema's full field set. A field
the tool does not take cannot be declared.

The comparison reads the **document** and the **server**, not the schema
against itself, so it cannot pass by checking the schema against itself.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from opspilot.adapters.tools.mcp_gateway import build_in_process_servers
from opspilot.domain.tools import (
    TOOL_ARGUMENT_SCHEMAS,
    TOOL_REGISTRY,
    InvalidArgumentsError,
    validate_tool_call,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "mcp-contracts.md"

#: A placeholder that satisfies any string field, so validation is reached.
_SAMPLE = "x"


def _string_end(text: str, start: int) -> int:
    """The index just past the string literal that starts at ``start``.

    A literal ends at the next unescaped quote. The escape is skipped whole so
    a ``\\"`` inside the literal does not look like the end of it.
    """
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return len(text)


def _top_level_keys(text: str) -> list[str]:
    """The keys of the objects written in a block, at the objects' own level.

    Scanning for brace-balanced objects rather than reading line by line: a
    JSONC object written over several lines contributes all of its keys, and
    one written on a single line contributes the same. Reading line by line is
    what left ``billing.issue_refund`` -- the one tool whose arguments move
    money -- invisible to this file, because its block opens a brace on a line
    of its own and the keys are on the lines after it.

    Only an object's own keys are taken. A key inside a nested object or array
    is reached through its parent, so a key counts only where the nesting
    depth is one.
    """
    keys: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            end = _string_end(text, index)
            if depth == 1 and re.match(r"\s*:", text[end:]):
                name = text[index + 1 : end - 1]
                if name not in keys:
                    keys.append(name)
            index = end
            continue
        if char == "/" and text[index + 1 : index + 2] == "/":
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        index += 1
    return keys


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

        names[tool] = _top_level_keys(input_side)
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
        if _runtime_supplied(tool, name):
            continue
        try:
            schema.model_validate(_sample_payload(schema, name))
        except ValidationError:
            forbidden.append(name)

    assert forbidden == [], (
        f"the contract documents these parameters for `{tool}` but its gate-1 "
        f"schema ({schema.__name__}) rejects them: {forbidden}. A model that "
        f"proposes a contract-valid call is refused before the tool runs -- the "
        f"drift `TransactionListArgs` records having survived four milestones. "
        f"Schema declares: {sorted(schema.model_fields)}"
    )


def _sample_value(annotation: object) -> object:
    """A value of a field's own annotation, so validation is reached."""
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if get_origin(annotation) is list:
        return [_SAMPLE]
    return _SAMPLE


def _sample_payload(model: type[BaseModel], name: str) -> dict[str, Any]:
    """A payload that exercises ``name`` and satisfies the schema's other rules.

    The parameter under test is validated *alongside* the schema's required
    fields, not on its own. ``issues.create`` requires ``title``, so a payload
    of ``{"description": "x"}`` alone is refused for a reason that has nothing
    to do with ``description`` -- and the guard would report a schema that is
    exactly right as one that forbids a documented parameter.

    Optional fields are left out, so the payload is the one a model would send:
    a schema whose optional field is present is a different question, and the
    reverse guard below is what covers that side.
    """
    payload: dict[str, Any] = {}
    for field_name, field in model.model_fields.items():
        if field_name != name and not field.is_required():
            continue
        payload[field_name] = _sample_value(field.annotation)
    return payload


def _runtime_supplied(tool: str, parameter: str) -> bool:
    """Whether the parameter is the runtime's to supply rather than the model's.

    A tool that requires an idempotency key gets one derived at gate 4 from the
    run and the intended effect, and the runtime injects it at dispatch
    (``docs/tool-permissions.md`` §4). The model may not choose it: accepting
    one would let a proposal pick a key that defeats the duplicate check. So the
    ``idempotency_key`` the contract documents is a parameter of the *server*,
    and deliberately not one of the gate-1 schema's.

    Derived from the registry rather than transcribed, so a tool that stops
    requiring a key stops being exempt.
    """
    spec = TOOL_REGISTRY.get(tool)
    return parameter == "idempotency_key" and spec is not None and spec.requires_idempotency_key


def test_the_idempotency_key_exemption_is_exactly_the_runtime_supplied_one() -> None:
    """The exemption is narrow, and the schema really does refuse the key.

    An exemption in a guard is a hole in it, so it is pinned from both sides:
    the one parameter it covers is the one the runtime supplies, and the schema
    really does refuse it. Widen the exemption and this fails; narrow it and
    the guard above starts rejecting the contract's own example.

    The refusal is checked on a payload that is otherwise valid, so it is the
    *key* that is refused and not some other field the payload happens to be
    missing.
    """
    schema = TOOL_ARGUMENT_SCHEMAS["billing.issue_refund"]
    without_key = _sample_payload(schema, "transaction_id")
    assert "idempotency_key" not in without_key
    schema.model_validate(without_key)  # the payload is valid without the key

    with pytest.raises(ValidationError):
        schema.model_validate({**without_key, "idempotency_key": _SAMPLE})

    assert _runtime_supplied("billing.issue_refund", "idempotency_key") is True
    assert _runtime_supplied("billing.issue_refund", "transaction_id") is False
    assert _runtime_supplied("billing.issue_refund", "amount") is False
    assert _runtime_supplied("billing.issue_refund", "currency") is False
    # A tool that does not require a key gets no exemption, even for one.
    assert _runtime_supplied("issues.create", "idempotency_key") is False


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


# ---------------------------------------------------------------------------
# The other direction: schema -> the real tool
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def servers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The real MCP servers, in process, over one private store directory.

    The servers are built for real rather than read from the document, because
    the parameter set that matters is the one the server *generates*: it is
    derived from the tool's signature, and it is the only thing that says what
    the server will actually accept.
    """
    return dict(build_in_process_servers(tmp_path_factory.mktemp("contract-servers")))


async def test_every_gate_1_field_is_a_parameter_the_server_actually_takes(
    servers: dict[str, Any],
) -> None:
    """The schema may not declare a field the tool has no parameter for.

    The direction the other guards were missing. A field here is not a call
    that fails -- it is a call that *succeeds*, because the MCP layer ignores
    the fields it does not recognise rather than refusing them (the generated
    schema carries no ``additionalProperties: false``). So the field is
    invisible to every behavioural check and only a comparison of the declared
    names can see it.

    And an ignored field is not harmless: it is a field the approver reads.
    ``RefundArgs.currency`` was one, and it put a currency into the
    ``risk_explanation`` and into the arguments snapshot for a refund the
    server was going to make in the transaction's own currency.

    The comparison is against the server's **own generated schema**, so it
    covers optional fields as well as required ones -- which is where the
    defect lived.
    """
    failures: list[str] = []

    for name, model in sorted(TOOL_ARGUMENT_SCHEMAS.items()):
        spec = TOOL_REGISTRY[name]
        if spec.server == "internal":
            # `knowledge.search` is served in-process by the knowledge adapter,
            # not by an MCP server (``docs/mcp-contracts.md`` §4), so there is
            # no generated schema to compare against.
            continue
        server = servers.get(spec.server)
        assert server is not None, f"{name} names server {spec.server!r} but none was built"
        listed = await server.list_tools()
        tool = next(t for t in listed if t.name == name)
        server_fields = set(tool.input_schema.get("properties", {}))

        # What the schema puts on the wire, and what it declares but drops.
        sent = {f for f, info in model.model_fields.items() if not info.exclude}
        dropped = set(model.model_fields) - sent

        unknown = sent - server_fields
        if unknown:
            failures.append(
                f"{name}: the gate-1 schema puts {sorted(unknown)} on the wire, "
                f"and the server has no parameter for them -- it ignores them, "
                f"so they would appear in the approver's snapshot and in the "
                f"risk explanation while changing nothing about the call. "
                f"The server takes: {sorted(server_fields)}"
            )
        swallowed = dropped & server_fields
        if swallowed:
            failures.append(
                f"{name}: the gate-1 schema drops {sorted(swallowed)}, and the "
                f"server does take them -- a real parameter cannot be left out "
                f"of the call"
            )

    assert not failures, (
        "these gate-1 schemas declare arguments the real tools do not accept -- "
        "the two sides have drifted apart:\n  " + "\n  ".join(failures)
    )


async def test_the_gate_1_schema_and_the_server_declare_the_same_parameters(
    servers: dict[str, Any],
) -> None:
    """The two sides agree exactly, field for field.

    The guard above is one-sided by design -- it reports a schema that offers
    the tool something it cannot take. This is the other half: a schema that
    *cannot* offer the tool something it does take. Together they are an
    equality, and the equality is what makes the contract and the code one
    description rather than two.

    ``billing.issue_refund`` is the one tool whose sets differ on purpose:
    the server requires an ``idempotency_key`` that gate 4 derives and the
    runtime injects, and the model may not supply one
    (``docs/tool-permissions.md`` §4). So the server's parameters minus the
    key are the schema's parameters, exactly.
    """
    failures: list[str] = []

    for name, model in sorted(TOOL_ARGUMENT_SCHEMAS.items()):
        spec = TOOL_REGISTRY[name]
        if spec.server == "internal":
            continue
        server = servers.get(spec.server)
        assert server is not None, f"{name} names server {spec.server!r} but none was built"
        listed = await server.list_tools()
        tool = next(t for t in listed if t.name == name)
        server_fields = set(tool.input_schema.get("properties", {}))

        if spec.requires_idempotency_key:
            # The runtime's to supply. Not the model's, so not the schema's.
            server_fields = server_fields - {"idempotency_key"}

        declared = {f for f, info in model.model_fields.items() if not info.exclude}
        if declared != server_fields:
            failures.append(
                f"{name}: the gate-1 schema declares {sorted(declared)} and the "
                f"server takes {sorted(server_fields)}"
            )

    assert not failures, (
        "these gate-1 schemas do not declare exactly the parameters their tools "
        "take -- one of the two sides is wrong:\n  " + "\n  ".join(failures)
    )
