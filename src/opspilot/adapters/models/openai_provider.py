"""``OpenAIModelProvider``: the OpenAI SDK behind the ``ModelProvider`` port.

Responsibility: call OpenAI models and return schema-validated results. Selected
by ``MODEL_PROVIDER=openai`` with ``OPENAI_API_KEY`` set.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

The ``openai`` SDK is imported **inside each method**, never at module top
level, so this module imports cleanly when the SDK is not installed. The core
workflow, the full test suite and the golden-path demo run with neither provider
SDK present; the optional extra in ``pyproject.toml`` is the guard.

API shape verified against the installed SDK (``openai`` 3.x): structured output
is ``client.beta.chat.completions.parse(response_format=<PydanticModel>)``, which
returns ``choices[0].message.parsed`` already validated. Prompt assembly,
schema-to-JSON conversion and usage accounting are shared with the Anthropic
adapter in ``_shared``.

Two OpenAI-compatible wrinkles are worth naming, because both are invisible
against real OpenAI. Some endpoints accept the same ``json_schema`` envelope and
answer HTTP 200, but return the model's JSON in a separate ``reasoning_content``
field and leave ``content`` as prose; the reply is therefore parsed from
``content`` first and, only if that will not validate, from ``reasoning_content``
(see ``_parse_reply``). Others accept the same envelope, answer 200, and then
*ignore the schema entirely* -- measured against MiniMax with the eval's
``TicketClassification``, one reply was valid JSON under keys the model had
invented and the next was Markdown prose, so 0 of 20 cases could run. The same
endpoint honours ``{"type": "json_object"}``, and asked that way returned
exactly the requested keys every time.

The second kind is answered by sending the *first* request differently, not by
asking again: an operator opts in with ``OPENAI_JSON_OBJECT_FALLBACK`` (default
off) and the structured request then goes out as ``json_object`` with the schema
written into the system prompt. One place decides it --
``_request_structured`` -- and either way there is exactly one request
per call.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from opspilot.adapters.models import _shared
from opspilot.ports.model_provider import ModelResponse, TModel

if TYPE_CHECKING:
    # Annotation-only: the runtime import stays function-local so this module (and
    # therefore the package) imports without any agent-layer coupling at import.
    from opspilot.agents.schemas import ProposedAction

logger = logging.getLogger(__name__)

#: The envelope the opt-in asks for *instead of* ``_response_format``'s, and
#: the only one it substitutes: ``json_object`` says "be JSON" without naming the
#: fields, which is the only thing an endpoint measured ignoring ``json_schema``
#: still honours. It carries no schema, so the schema goes in the prompt instead
#: -- see ``_schema_in_system_prompt``.
_JSON_OBJECT_RESPONSE_FORMAT: dict[str, Any] = {"type": "json_object"}


class MissingAPIKeyError(RuntimeError):
    """The provider was constructed without an API key.

    Raised eagerly from each call so a misconfigured deployment fails with a
    named error at first use, not deep inside the SDK with an opaque one.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"{provider} provider has no API key configured; set the provider's "
            f"API key environment variable or select MODEL_PROVIDER=fake"
        )


class OpenAIModelProvider:
    """OpenAI-backed ``ModelProvider``. Imports the SDK lazily."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float = 60.0,
        base_url: str = "",
        json_object_fallback: bool = False,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url
        self._json_object_fallback = json_object_fallback

    def _client(self) -> Any:
        """Build the async client, importing the SDK here and checking the key.

        The key check precedes the import so a missing key raises a named error
        even on a machine without the SDK -- the message is the same either way.

        ``base_url`` is passed only when non-empty. An empty string is a
        *different* argument from an omitted one: the SDK treats ``""`` as an
        endpoint rather than "use the default", so passing it unconditionally
        would break every plain-OpenAI deployment. Omitting it leaves the SDK's
        own ``https://api.openai.com/v1`` in place.
        """
        if not self._api_key:
            raise MissingAPIKeyError("openai")
        from openai import AsyncOpenAI

        if self._base_url:
            return AsyncOpenAI(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                base_url=self._base_url,
            )
        return AsyncOpenAI(api_key=self._api_key, timeout=self._timeout_seconds)

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Call OpenAI and parse the result into ``schema``.

        Uses the SDK's structured-output ``parse`` path with the Pydantic model
        as ``response_format``; the SDK validates and returns
        ``message.parsed``. A ``None`` or schema-invalid result raises via
        ``_shared.parse_structured`` -- never a partial.
        """
        client = self._client()
        started = time.perf_counter()
        completion, value = await self._request_structured(
            client,
            system=system,
            prompt=prompt,
            schema=schema,
            timeout_seconds=timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = completion.usage
        return ModelResponse[TModel](
            value=value,
            usage=_shared.build_usage(
                provider="openai",
                # The model the *response* reports, not the one requested: an
                # OpenAI-compatible endpoint (a gateway, a router) may answer for
                # a different model, and a results file naming the requested one
                # would attribute the numbers to a model that did not produce
                # them. Fall back to the configured name only when omitted.
                model=_shared.reported_model(completion, self._model_name),
                latency_ms=latency_ms,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
        )

    async def choose_tool(
        self,
        *,
        system: str,
        prompt: str,
        available_tools: list[str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Call OpenAI and return a tool proposal as an unvalidated dict.

        Requests structured output through the **same** mechanism as
        ``generate_structured``: the Pydantic class in ``response_format``, which
        the SDK expands into a well-formed
        ``{"type": "json_schema", "json_schema": {...}}`` envelope. Handing the
        SDK the *bare* JSON Schema instead -- ``_shared.json_schema_for`` -- is
        the trap here: the SDK forwards a dict unchanged, so the request went out
        with ``response_format.type="object"`` and every tool-selection call
        returned 400 ``unknown variant 'object'``. There is now one spelling of
        this request in this module, and a boundary test holds it there.

        The proposal is returned unvalidated on purpose: gate 1 and gate 2 decide
        whether it is acceptable, and the port's contract says the result is
        untrusted until then. ``tool_choice`` is left to the model; a proposal
        naming an unregistered tool is a legal (and testable) outcome.
        """
        model = _tool_proposal_model()
        client = self._client()
        _completion, proposal = await self._request_structured(
            client,
            system=system,
            # The tool menu stays in the *user* message whichever envelope the
            # request ends up carrying. It is a hint about what may be called,
            # and the opt-in changes only the *system* message, so the model is
            # told which tools exist either way.
            prompt=_shared.with_tool_menu(prompt, available_tools),
            schema=model,
            timeout_seconds=timeout_seconds,
        )
        dumped: dict[str, Any] = proposal.model_dump()
        return dumped

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Call OpenAI and return free-form text, used only for the customer reply."""
        client = self._client()
        completion = await client.chat.completions.create(
            model=self._model_name,
            messages=_shared.render_messages(_shared.default_system(system), prompt),
            timeout=timeout_seconds or self._timeout_seconds,
        )
        return completion.choices[0].message.content or ""

    async def _request_structured[TModel: BaseModel](
        self,
        client: Any,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None,
    ) -> tuple[Any, TModel]:
        """Send the structured request, in the shape the operator configured.

        Both structured paths call this, so ``choose_tool`` inherits the opt-in
        rather than spelling a second one.

        **Off, which is the default.** The request is exactly what it has always
        been: the ``json_schema`` envelope from :func:`_response_format`, parsed
        by :func:`_parse_reply`. No branch is taken, no envelope is rewritten
        and no prompt is altered, so a real OpenAI deployment sends the same
        bytes it sent before this setting existed.

        **On.** The *first* request asks for ``json_object`` instead, with the
        schema written into the system prompt (:func:`_schema_in_system_prompt`)
        -- and it is still the only request.

        That shape is what live testing settled, and it is deliberately not
        "ask again when the reply is bad". Measured against MiniMax with the
        eval's ``TicketClassification``, the ``json_schema`` envelope is accepted
        and then ignored: one reply came back as valid JSON under keys the model
        had invented, and the next as Markdown prose. The prose set
        ``StructuredOutputError.decoded`` to ``False``, so a retry gated on a
        *decoded* reply declined to fire and the run failed on the first request
        anyway -- the dead end this shape removes. Asking the other way,
        ``json_object`` with the schema in the prompt, returned exactly the
        requested keys on every case tried.

        The cost follows the shape: one request per call under both settings.
        For an endpoint that needed the opt-in that is cheaper than asking twice
        (one billable call rather than two), and it is not what real OpenAI needs
        at all, which is why it stays opt-in.

        Validation is not weakened by any of this. The reply goes through the
        same :func:`_shared.parse_structured` against the same model as before,
        so ``extra="forbid"`` still forbids: the opt-in widens what the endpoint
        is *asked*, never what is accepted. A wrong-shaped reply raises, which is
        what a schema-invalid answer does everywhere else
        (``docs/agent-state-machine.md`` §3).

        Returns the completion that produced the value alongside the value
        itself, so usage is accounted to the answer actually returned.
        """
        timeout = timeout_seconds or self._timeout_seconds
        system = _shared.default_system(system)

        response_format: Any
        if self._json_object_fallback:
            logger.debug(
                "OPENAI_JSON_OBJECT_FALLBACK is on: asking for json_object with the "
                "%s schema in the system prompt instead of a json_schema envelope",
                schema.__name__,
            )
            response_format = _JSON_OBJECT_RESPONSE_FORMAT
            # Asked as prose rather than as a constraint, which is what makes the
            # request *different* rather than a retry of the first one. The safety
            # framing the caller supplied is still there -- the schema is appended
            # to it rather than replacing it, so nothing that made the request safe
            # is lost.
            system = _schema_in_system_prompt(system, schema)
        else:
            response_format = _response_format(schema)

        completion = await client.beta.chat.completions.parse(
            model=self._model_name,
            messages=_shared.render_messages(system, prompt),
            response_format=response_format,
            timeout=timeout,
        )
        return completion, _parse_reply(completion.choices[0].message, schema)


def _schema_in_system_prompt(system: str, schema: type[BaseModel]) -> str:
    """``system`` plus the JSON Schema ``schema`` asks for, in words.

    ``json_object`` constrains nothing, so the shape has to be said somewhere or
    the model has only its own judgement of what the reply should look like.
    Rendered through :func:`_shared.json_schema_for` -- Pydantic's own schema,
    the same one :func:`_response_format` puts in the envelope -- so the prompt
    describes the schema the opt-in swapped out, and the two spellings cannot
    drift.

    The schema is indented under a fenced block so a reply that *quotes* the
    instructions back cannot be confused with the instructions.
    """
    rendered = json.dumps(_shared.json_schema_for(schema), indent=2, sort_keys=True)
    return (
        f"{system}\n\n"
        f"Respond with a single JSON object and nothing else. It must match this "
        f"JSON Schema exactly: every required field present, no field outside "
        f"'properties', and no surrounding prose or Markdown fence.\n\n"
        f"```json\n{rendered}\n```"
    )


def _response_format(schema: type[BaseModel]) -> Any:
    """The ``response_format`` envelope for ``schema``, as the SDK would build it.

    Handing ``parse`` the Pydantic class is the spelling this module has always
    used, and the request it produces is correct -- but the SDK then parses
    ``message.content`` itself, inside the ``await``, and lets a Pydantic
    ``ValidationError`` escape when that content is not the schema. The
    exception is raised before the completion is returned, so the adapter never
    holds the message and never sees the sibling field. That is the whole defect:
    a reply the adapter would accept is unreachable because an eager parse
    upstream of it raised.

    Passing the *pre-expanded* envelope instead leaves the request byte-identical
    -- the SDK's own converter is what expanded the class in the first place, so
    the same body goes out, verified in
    ``test_the_request_is_unchanged_by_the_envelope`` -- and, because a dict is
    not a rich response format, ``parse`` stops parsing and returns the message
    as it arrived. Validation moves to :func:`_parse_reply`, where both fields
    are in reach. Only the eager parse is lost; this module already declined to
    read ``message.parsed``, so nothing depended on it.

    A private SDK helper, imported inside this function like the SDK import
    itself: it is the one thing that produces the exact envelope the class would
    have produced, so re-deriving it here would be a second spelling of the
    request -- the defect ``tests/unit/test_openai_provider_boundary.py`` exists
    to prevent.
    """
    from openai.lib._parsing import type_to_response_format_param

    return type_to_response_format_param(schema)


def _parse_reply[TModel: BaseModel](message: Any, schema: type[TModel]) -> TModel:
    """Validate the reply in ``message.content``, then ``reasoning_content``.

    Both structured paths parse through here, so ``choose_tool`` inherits the
    fallback rather than spelling a second one.

    ``content`` is tried first and is what succeeds against real OpenAI.
    ``reasoning_content`` is read only after that has already failed: some
    OpenAI-compatible endpoints accept the ``json_schema`` envelope, answer 200,
    and put the model's JSON *there* while ``content`` carries the prose summary
    -- against MiniMax every case in the classification eval then failed
    ``Invalid JSON`` and 0 of 20 could run. It is one extra field of one
    documented response shape, not a search over the message for anything that
    parses: no regex extraction, no retries, no other fields.

    The fallback payload goes through the same
    ``_shared.parse_structured`` validation as the primary one, so a reply that
    ignores the requested schema still fails. Absent, empty, or invalid, the
    error from ``content`` propagates unchanged -- the caller is told its own
    reply did not parse, not that a field it never asked about did, and never
    gets a partial.
    """
    try:
        return _shared.parse_structured(message.content or "", schema)
    except _shared.StructuredOutputError as primary:
        # Read by name and guarded on type: the SDK's message model does not
        # declare the field, so this is a dynamic shape detail by construction.
        reasoning = getattr(message, "reasoning_content", None)
        if not isinstance(reasoning, str) or not reasoning:
            raise
        try:
            return _shared.parse_structured(reasoning, schema)
        except _shared.StructuredOutputError:
            raise primary from None


def _tool_proposal_model() -> type[ProposedAction]:
    """The Pydantic model the tool-proposal JSON is parsed into.

    A local import of the agent schema: ``ProposedAction`` is the value object a
    proposal is, and reusing it keeps the fake and both real providers aligned on
    the same shape.

    The class is used for both *requesting* and *validating* the proposal. There
    is deliberately no companion ``_tool_proposal_schema`` helper: handing
    ``model_json_schema()`` to the OpenAI SDK produces a request the API rejects,
    so the schema is the SDK's business, not this module's. See
    ``choose_tool``.
    """
    from opspilot.agents.schemas import ProposedAction

    return ProposedAction
