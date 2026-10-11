"""The ``json_object`` opt-in: the first request asks the working way. Off by default.

An OpenAI-compatible endpoint can accept the ``json_schema`` envelope, answer
HTTP 200, and then ignore the schema. Measured against MiniMax with the eval's
``TicketClassification``, two different non-conforming replies came back from
otherwise-identical requests: valid JSON under *keys the model invented* --
``{"category": "duplicate_charge", "invoice_id": ..., "amount": ...}``, two
required fields missing and three forbidden extras -- and, on another call,
Markdown prose. Neither is reachable from anything the adapter can do after the
fact, and between them they cost 0 of 20 runnable classification cases.

The same endpoint *does* honour ``response_format={"type": "json_object"}``,
which asks for JSON without naming the fields. With the schema described in the
prompt instead, it returned exactly ``{"category", "confidence", "rationale"}``.

So the fix is the *first* request, not a second one. These tests pin what the
opt-in is allowed to be, in the order it matters:

- **Off by default, and off means untouched.** The request still carries the
  ``json_schema`` envelope and the caller's system prompt verbatim. This is the
  load-bearing assertion: real OpenAI constrains generation, and an opt-in that
  nobody took must not alter a single byte of what those deployments send.
- **On means the first request, in one call.** ``json_object``, the schema
  fenced into the system prompt, and exactly one request -- never a good
  attempt followed by a better one.
- **No weaker validation.** The opt-in widens what the endpoint is *asked*,
  never what is accepted: a wrong-shaped reply raises on the same
  ``_shared.parse_structured`` against the same model, on the same request
  count.

The retry-on-failure shape is what this file used to pin, and it is the shape
the measurement killed: the prose reply sets ``decoded`` to ``False``, so a
fallback gated on "the answer was JSON" declined to fire and the run failed on
the first request anyway. The ``decoded`` attribute stays on
``StructuredOutputError``; nothing in this module reads it any more, and the
assertions below pin what that costs (still one request, still a raised error)
rather than the condition that has gone.

Driven against a genuine ``AsyncOpenAI`` over a recording httpx transport, as in
``test_openai_provider_reasoning_content_fallback.py``: the SDK's own request
serialization and response parsing run for real, so what is asserted here is
what the adapter caused to be sent. No network, no API key, no secret read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from opspilot.adapters.models import _shared
from opspilot.adapters.models.openai_provider import OpenAIModelProvider
from opspilot.agents.schemas import TicketClassification

_CLASSIFICATION_JSON = json.dumps(
    {"category": "duplicate_charge", "confidence": 0.99, "rationale": "charged twice"}
)

# The observed violation, verbatim: JSON the model produced, under keys the
# schema does not have. `extra="forbid"` makes all three extras failures of
# their own, so this cannot validate by accident.
_INVENTED_KEYS_JSON = json.dumps(
    {
        "category": "duplicate_charge",
        "invoice_id": "INV-2026-384",
        "amount": 129.0,
        "priority": "high",
        "summary": "customer reports a duplicate charge",
    }
)

# The other observed reply to the same request: Markdown, not JSON. This is the
# case that made retry-on-failure the wrong shape -- it is not JSON at all, so a
# fallback gated on `decoded` never fired for it.
_MARKDOWN_REPLY = "**Classification:** Billing & payments\n**Issue type:** Duplicate charge"

#: Builds the ``message`` dict to serve, from the schema name the request named.
_ReplyFor = Callable[[str], dict[str, Any]]


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Records each request body, then answers with the message ``reply_for`` builds.

    ``reply_for`` is called once per request and receives the schema name the
    request named -- the schema's own name under the default ``json_schema``
    envelope, and empty under the ``json_object`` opt-in, which names no schema.
    """

    def __init__(self, reply_for: _ReplyFor) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._reply_for = reply_for

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content.decode())
        self.bodies.append(sent)
        name = (sent.get("response_format", {}) or {}).get("json_schema", {}).get("name", "")
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "test-model",
            "choices": [{"index": 0, "message": self._reply_for(name), "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return httpx.Response(200, json=body)


def _provider_answering(
    monkeypatch: pytest.MonkeyPatch,
    transport: _RecordingTransport,
    *,
    json_object_fallback: bool,
) -> OpenAIModelProvider:
    """A real provider whose SDK client records and answers instead of sending.

    Only the SDK *constructor* is replaced; the client is a genuine
    ``AsyncOpenAI``, so ``parse``'s own serialization and response handling run
    exactly as they do in production. ``json_object_fallback`` is passed
    explicitly rather than read from the environment: the setting's default is
    what one case below is about, and an ambient ``OPENAI_JSON_OBJECT_FALLBACK``
    in a developer's shell would otherwise decide it.
    """
    import openai

    real_async_openai = openai.AsyncOpenAI

    def _build(**kwargs: object) -> object:
        return real_async_openai(
            api_key=str(kwargs.get("api_key", "")),
            base_url="https://example.invalid/v1",
            # The SDK's stubs are generated against `httpx2`, while the installed
            # httpx is `httpx1`; they are the same runtime object, and the stub
            # alias is an artifact of the SDK's codegen.
            http_client=httpx.AsyncClient(transport=transport),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", _build)
    return OpenAIModelProvider(
        api_key="test-key",
        model_name="test-model",
        json_object_fallback=json_object_fallback,
    )


def _answers(*contents: str) -> _ReplyFor:
    """A ``reply_for`` that serves ``contents`` in order, repeating the last.

    One request per call now, so the sequence exists for the assertions about
    request *count*, not to script a recovery: the last entry repeating means a
    regression that asked twice would still be answered, and the count would
    fail as an assertion rather than as an IndexError inside the transport.
    """
    seen = 0

    def _reply_for(_name: str) -> dict[str, Any]:
        nonlocal seen
        content = contents[min(seen, len(contents) - 1)]
        seen += 1
        return {"role": "assistant", "content": content}

    return _reply_for


# -- the default is unchanged ------------------------------------------------


async def test_the_default_request_still_carries_the_json_schema_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing assertion: off means the request is untouched.

    Real OpenAI constrains generation, so the whole safety of an opt-in is that
    an operator who never set it keeps sending exactly what they sent before.
    This is asserted against the *conforming* reply, because that is the case
    the default path exists to serve: a working reply must not have its request
    rewritten around it.

    Two halves, and the second is the one a fix here would plausibly break: the
    envelope is still ``json_schema``, and the caller's system prompt is still
    the caller's. Rewriting the first request is exactly how an endpoint gets
    supported by changing what every OpenAI deployment sends.
    """
    transport = _RecordingTransport(_answers(_CLASSIFICATION_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=False)

    await provider.generate_structured(
        system="you are a triage classifier", prompt="classify this", schema=TicketClassification
    )

    assert len(transport.bodies) == 1, "expected the one structured request and nothing else"
    sent = transport.bodies[0]

    assert sent["response_format"]["type"] == "json_schema", (
        f"the default request carries {sent['response_format'].get('type')!r}; "
        f"an unset OPENAI_JSON_OBJECT_FALLBACK must leave the json_schema envelope "
        f"exactly as it was"
    )
    assert sent["response_format"]["json_schema"]["name"] == "TicketClassification"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["messages"][0] == {
        "role": "system",
        "content": "you are a triage classifier",
    }, (
        "the default path rewrote the caller's system prompt. The schema only "
        "travels in the prompt when json_object replaces the envelope; when the "
        "envelope is present, the prompt is the operator's own words."
    )
    assert sent["messages"][-1] == {"role": "user", "content": "classify this"}


async def test_a_schema_violating_reply_fails_after_one_request_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad reply still costs exactly one request, and still raises.

    The transport answers unfixably bad JSON, so this cannot pass for the wrong
    reason: a reply that merely needed the opt-in would also raise here, and the
    request count is the whole assertion.
    """
    transport = _RecordingTransport(_answers(_INVENTED_KEYS_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=False)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.generate_structured(
            system="s", prompt="classify this", schema=TicketClassification
        )

    assert excinfo.value.schema_name == "TicketClassification"
    assert len(transport.bodies) == 1, (
        "the opt-in was active with OPENAI_JSON_OBJECT_FALLBACK unset; every "
        "classification against a provider that needs no help now sends a "
        "different request than it used to"
    )


async def test_the_constructor_default_is_the_json_schema_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter's own default, not just what the wiring passes.

    Two of the three construction sites are in tests, and a default only the
    worker sets correctly is a default the other one quietly drops.
    """
    transport = _RecordingTransport(_answers(_CLASSIFICATION_JSON))
    # The SDK constructor is patched first, so the provider below -- built
    # without the argument, which is the case under test -- still records its
    # request through the same transport.
    _provider_answering(monkeypatch, transport, json_object_fallback=True)
    provider = OpenAIModelProvider(api_key="test-key", model_name="test-model")

    await provider.generate_structured(
        system="s", prompt="classify this", schema=TicketClassification
    )

    assert len(transport.bodies) == 1
    assert transport.bodies[0]["response_format"]["type"] == "json_schema"


# -- the opt-in, when the operator has asked for it --------------------------


async def test_the_first_request_asks_for_json_object_with_the_schema_in_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the request must look like for the opt-in to be worth setting.

    Two halves, and neither is optional. ``json_object`` alone asks only "be
    JSON", which is the half the measured endpoint honours. The schema in the
    system prompt is what tells it *which* JSON -- with the envelope ignored,
    nothing else in the request carries the shape, and without the prompt the
    model returns exactly the keys it invented the first time.

    The request count is asserted here rather than in a test of its own: this is
    the one request the feature makes, and the first is what is under test.
    """
    transport = _RecordingTransport(_answers(_CLASSIFICATION_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    await provider.generate_structured(
        system="you are a triage classifier",
        prompt="classify this",
        schema=TicketClassification,
    )

    assert len(transport.bodies) == 1, (
        f"the opt-in made {len(transport.bodies)} requests. The first request is "
        f"the one that asks json_object; there is no second attempt, and any "
        f"extra request here is an extra billable call."
    )
    sent = transport.bodies[0]
    assert sent["response_format"] == {"type": "json_object"}

    system = sent["messages"][0]["content"]
    assert sent["messages"][0]["role"] == "system"
    assert "you are a triage classifier" in system, (
        "the schema-in-prompt request dropped the caller's system prompt; the "
        "safety framing an operator wrote would no longer apply at all"
    )
    for field in ("category", "confidence", "rationale"):
        assert field in system, f"the schema rendered into the prompt omits {field!r}"
    assert '"required"' in system, "the schema was described without its required fields"
    assert "```json" in system, (
        "the schema is not fenced, so a reply quoting the instructions back could "
        "be confused with the instructions"
    )
    assert sent["messages"][-1] == {"role": "user", "content": "classify this"}


async def test_the_value_is_validated_against_the_same_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A returned value is the real thing, parsed by the shared validating path.

    Not asserted by checking that no exception was raised -- by checking that the
    value is a ``TicketClassification`` carrying the fields the model sent. A
    salvage that returned the parsed JSON as a dict, or dropped ``extra="forbid"``
    on the way, would pass a weaker test and reach gate 1 half-formed.
    """
    transport = _RecordingTransport(_answers(_CLASSIFICATION_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    response = await provider.generate_structured(
        system="s", prompt="classify this", schema=TicketClassification
    )

    assert isinstance(response.value, TicketClassification)
    assert response.value.category == "duplicate_charge"
    assert response.value.confidence == 0.99
    assert response.value.rationale == "charged twice"


async def test_usage_names_the_reply_that_was_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage recorded is the request that produced the value.

    This file's earlier shape made the completion an easy thing to get wrong --
    two of them existed, and the wrong one could be returned. With one request
    per call that is no longer ambiguous, and the assertion stays because
    ``_request_structured`` still hands back a completion alongside the
    value, and a caller reading usage from somewhere else would still attribute
    the run to a reply it did not use.
    """
    transport = _RecordingTransport(_answers(_CLASSIFICATION_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    response = await provider.generate_structured(
        system="s", prompt="p", schema=TicketClassification
    )

    assert response.usage.provider == "openai"
    assert response.usage.model == "test-model"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5


# -- the opt-in does not rescue what it was not for --------------------------


async def test_an_invented_keys_reply_still_fails_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed defect, fed back in: it still raises, and still costs one call.

    The reply here is the real one from the endpoint that needed the opt-in, so
    this is the case that proves the flag changes the *request* and not the
    *verdict*: an endpoint that answers this way against ``json_object`` fails
    the run loudly, as a schema-invalid answer does everywhere else
    (``docs/agent-state-machine.md`` §3), instead of reaching gate 1 as a
    half-formed value.
    """
    transport = _RecordingTransport(_answers(_INVENTED_KEYS_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.generate_structured(
            system="s", prompt="classify this", schema=TicketClassification
        )

    assert excinfo.value.schema_name == "TicketClassification"
    assert len(transport.bodies) == 1, (
        f"a schema-violating reply made {len(transport.bodies)} requests. There "
        f"is no retry: the prose reply is the case one could not have fixed, so "
        f"the adapter asks the working way once instead of asking twice."
    )


async def test_extra_forbid_is_not_weakened_by_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply of the right shape plus one invented field must still raise.

    ``json_object`` constrains nothing, so this is where that could turn a strict
    schema into a permissive one: the reply answers in the schema's own keys and
    adds one more. If it were accepted, the opt-in would be widening what is
    *accepted*, not only what is asked.
    """
    transport = _RecordingTransport(
        _answers(
            json.dumps(
                {
                    "category": "duplicate_charge",
                    "confidence": 0.99,
                    "rationale": "charged twice",
                    "priority": "high",
                }
            )
        )
    )
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    with pytest.raises(_shared.StructuredOutputError):
        await provider.generate_structured(
            system="s", prompt="classify this", schema=TicketClassification
        )

    assert len(transport.bodies) == 1


async def test_a_prose_reply_still_fails_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other observed reply: Markdown instead of JSON, and it raises.

    This is the case the measurement turned on. It sets ``decoded`` to ``False``,
    so the retry this file used to pin would have declined to fire -- the run
    would have failed on the first request, which is what the operator is
    running it to avoid. ``decoded`` is kept on the error because it remains a
    correct fact about the reply; nothing here reads it to choose a second
    request, because there is no second request.
    """
    transport = _RecordingTransport(_answers(_MARKDOWN_REPLY))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.generate_structured(
            system="s", prompt="classify this", schema=TicketClassification
        )

    assert "not valid JSON" in excinfo.value.detail
    assert excinfo.value.decoded is False
    assert len(transport.bodies) == 1, (
        "a reply that was not JSON at all triggered a second billable request"
    )


# -- choose_tool inherits it -------------------------------------------------


_PROPOSAL_JSON = json.dumps(
    {
        "tool_name": "search_knowledge",
        "arguments": {"query": "refund"},
        "reason": "r",
        "done": False,
    }
)

#: A tool proposal under keys ``ProposedAction`` does not have: the same defect
#: as the classification reply, on the planning step.
_INVENTED_TOOL_JSON = json.dumps({"tool": "search_knowledge", "q": "refund"})


async def test_the_tool_path_asks_for_json_object_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool selection gets the opt-in without a second spelling of it.

    Planning runs every iteration of every run, so an adapter that fixed only the
    classification call would fail such an endpoint at the first planning step.
    Both paths go through ``_request_structured``, and this is the
    assertion that they have not drifted apart: the tool menu must reach the
    model in the *user* message and the *proposal* schema in the system one.
    """
    transport = _RecordingTransport(_answers(_PROPOSAL_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    proposal = await provider.choose_tool(
        system="s", prompt="what next?", available_tools=["search_knowledge"]
    )

    assert proposal["tool_name"] == "search_knowledge"
    assert proposal["arguments"] == {"query": "refund"}
    assert len(transport.bodies) == 1
    assert transport.bodies[0]["response_format"] == {"type": "json_object"}
    assert "search_knowledge" in transport.bodies[0]["messages"][-1]["content"], (
        "the opt-in dropped the tool menu from the user message; the model is "
        "being asked to pick a tool without being told which ones exist"
    )
    assert "tool_name" in transport.bodies[0]["messages"][0]["content"], (
        "the tool path rendered the wrong schema into the prompt"
    )


async def test_the_tool_path_keeps_the_json_schema_envelope_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default, asserted on the planning path rather than only on the other one."""
    transport = _RecordingTransport(_answers(_PROPOSAL_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=False)

    proposal = await provider.choose_tool(
        system="s", prompt="what next?", available_tools=["search_knowledge"]
    )

    assert proposal["tool_name"] == "search_knowledge"
    assert len(transport.bodies) == 1
    assert transport.bodies[0]["response_format"]["type"] == "json_schema"
    assert transport.bodies[0]["response_format"]["json_schema"]["name"] == "ProposedAction"


async def test_a_schema_violating_proposal_still_fails_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning gets the same guarantees, which is the point of the shared method.

    ``choose_tool`` could have grown its own condition -- tool selection reads a
    *proposal*, and "not JSON" reads differently there than it does for a
    classification. One condition, in one place, is the thing being held.
    """
    transport = _RecordingTransport(_answers(_INVENTED_TOOL_JSON))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.choose_tool(system="s", prompt="p", available_tools=["search_knowledge"])

    assert excinfo.value.schema_name == "ProposedAction"
    assert len(transport.bodies) == 1


async def test_a_non_json_proposal_still_fails_on_the_tool_path_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prose on the planning path is a failure too, and costs one request."""
    transport = _RecordingTransport(_answers("I would search the knowledge base."))
    provider = _provider_answering(monkeypatch, transport, json_object_fallback=True)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.choose_tool(system="s", prompt="p", available_tools=["search_knowledge"])

    assert excinfo.value.decoded is False
    assert len(transport.bodies) == 1


# -- it reaches the process, or it is a setting nobody has -------------------


def test_the_worker_forwards_it_to_the_openai_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setting the composition root does not pass is a setting nobody set.

    Asserted against the constructed object rather than a spy, so a rename in
    the adapter surfaces here as an ``AttributeError`` instead of a silently
    unset value.
    """
    from opspilot.adapters.models.openai_provider import OpenAIModelProvider
    from opspilot.settings import get_settings
    from opspilot.worker import __main__ as worker_main

    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "probe-key-not-real")
    monkeypatch.setenv("OPENAI_JSON_OBJECT_FALLBACK", "true")
    get_settings.cache_clear()

    built = worker_main.build_worker_provider()
    assert isinstance(built, OpenAIModelProvider), (
        f"the worker built {type(built).__name__}, not an OpenAIModelProvider"
    )
    assert built._json_object_fallback is True, (
        "the worker built the OpenAI provider without OPENAI_JSON_OBJECT_"
        "FALLBACK; an operator who set it would be measured on the default "
        "path, and every case would fail for a reason the config does not show"
    )


def test_the_eval_runner_forwards_it_to_the_openai_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second assembly point, for the reason the Anthropic one names.

    ``worker/__main__.py`` and ``evals/runner.py`` construct the same adapter
    from the same settings in two separate functions, and an eval pointed at such
    an endpoint is exactly where the difference shows: every case fails while
    the worker's own configuration says the opt-in is on.
    """
    from opspilot.adapters.models.openai_provider import OpenAIModelProvider
    from opspilot.evals import runner
    from opspilot.settings import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "probe-key-not-real")
    monkeypatch.setenv("OPENAI_JSON_OBJECT_FALLBACK", "true")
    get_settings.cache_clear()

    built = runner._build_provider("openai")
    assert isinstance(built, OpenAIModelProvider), (
        f"the runner built {type(built).__name__}, not an OpenAIModelProvider"
    )
    assert built._json_object_fallback is True, (
        "the runner did not forward OPENAI_JSON_OBJECT_FALLBACK, so an eval "
        "would measure the default path while the deployment had opted into "
        "the other one -- the two halves disagreeing about their own config"
    )


# -- what it is not ----------------------------------------------------------


def test_the_structured_path_is_one_request_and_not_a_search() -> None:
    """Asserted against the source, because the guard is about *generalisation*.

    A behavioural test cannot tell "one request either way" from "ask again until
    something works": both answer the cases above. The failure being guarded is
    the second one appearing later, in the shape every retry helper grows into --
    a loop, a backoff, a list of envelopes searched in order. Each spends money a
    deployment did not agree to spend, and none of them is distinguishable from
    this one from outside.

    The call count is the load-bearing assertion: there is exactly one
    ``completions.parse`` in this method, so neither the flag nor any reply can
    produce a second request.
    """
    import ast
    import inspect
    import textwrap

    from opspilot.adapters.models import openai_provider

    source = inspect.getsource(openai_provider.OpenAIModelProvider._request_structured)
    tree = ast.parse(textwrap.dedent(source))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_request_structured"
    )
    assert ast.get_docstring(method) is not None

    statements = [node for node in method.body if not isinstance(node, ast.Expr)]
    body = "\n".join(line for node in statements for line in ast.unparse(node).splitlines())

    assert body.count("completions.parse(") == 1, (
        f"_request_structured makes "
        f"{body.count('completions.parse(')} requests. One call per structured "
        f"request is the invariant under both settings; a second one is a "
        f"billable call the operator did not agree to."
    )

    # Loops are checked on the tree, not the text: a `for` in prose -- a log
    # message, a comment -- is not a loop, and a substring guard cannot tell the
    # difference. A real loop would compile and behave identically while
    # tripping nothing.
    loops = [
        node for node in ast.walk(method) if isinstance(node, ast.For | ast.While | ast.AsyncFor)
    ]
    assert not loops, (
        f"_request_structured contains {len(loops)} loop(s). One request, "
        "shaped by the flag, is the whole feature; a loop here would make every "
        "schema failure cost an unbounded number of billable calls."
    )

    # These three are library calls rather than language constructs, so they are
    # checked on the text: `time.sleep` or a backoff helper would compile and
    # behave identically without tripping a node check. The docstring is dropped
    # above because it *names* the things the code must not do.
    for smuggled in ("backoff", "sleep(", "range("):
        assert smuggled not in body, (
            f"_request_structured mentions {smuggled!r}. One request, "
            "shaped by the flag, is the whole feature; a wait here would make "
            "every schema failure cost both money and latency."
        )


def test_the_fallback_is_configured_in_exactly_one_place() -> None:
    """One boolean, read in one place, so there is no second opinion.

    ``openai_json_object_fallback`` is the whole configuration surface. A second
    spelling of the same decision -- a per-call flag that can disagree with the
    constructor's, or the adapter reaching for the settings module -- would let a
    deployment believe it had opted out while the adapter had not.
    """
    import inspect

    from opspilot.adapters.models import openai_provider

    class_source = inspect.getsource(openai_provider.OpenAIModelProvider)
    assert class_source.count("self._json_object_fallback =") == 1, (
        "the setting is stored somewhere other than __init__, so construction and "
        "the request could disagree about what the operator configured"
    )
    assert class_source.count("if self._json_object_fallback:") == 1, (
        "the opt-in is read more than once. One branch in one method is what "
        "keeps the request shape a single decision; a second read is how this "
        "grows a per-call override that can disagree with the constructor"
    )
    assert "get_settings" not in class_source, (
        "the adapter receives its configuration; it does not import the settings "
        "module, or the same knob would have two sources of truth"
    )
