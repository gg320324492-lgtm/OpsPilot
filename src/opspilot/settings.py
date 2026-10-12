"""Application settings for OpsPilot.

Responsibility: the single, typed source of runtime configuration. Every
variable named in ``.env.example`` is read here and nowhere else, so a
misconfigured deployment fails loudly at startup rather than at first use.

Layer: leaf / cross-cutting. This module is imported by every layer (``api``,
``worker``, ``adapters``, ``agents``) but imports nothing above it -- it depends
only on ``pydantic-settings`` and the standard library. It deliberately does not
import ``domain`` or any adapter: configuration describes the process, not the
business rules.

The principles that show up here: configuration may *remove* capability (the
tool denylist) but never *grant* it -- no setting widens a tool permission, which
lives in static code (see ``docs/tool-permissions.md`` §2.1).
"""

from __future__ import annotations

import shlex
import sys
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelProviderName = Literal["fake", "anthropic", "openai"]
EmbeddingProviderName = Literal["local", "openai"]


def default_mcp_server_command(module: str) -> str:
    """The ``MCP_<NAME>_COMMAND`` value to fall back on: this interpreter.

    Uses ``sys.executable`` rather than a literal ``python`` because a bare
    ``python`` is whatever happens to be first on ``PATH``, which is frequently
    *not* the interpreter OpsPilot is running under. A virtualenv checkout is the
    normal case: the worker runs from ``.venv/bin/python`` while ``python`` on
    ``PATH`` is the system interpreter, whose site-packages has no ``mcp``. The
    server then fails to import with ``ModuleNotFoundError: No module named
    'mcp'`` -- which reads like a broken install and is not one, and which would
    be reported against the stdio transport that is in fact the only thing
    unusual about the deployment.

    Quoted with :func:`shlex.quote` because the interpreter path routinely
    contains spaces on Windows, and the consumer's parser treats quotes as
    grouping only.

    Args:
        module: The dotted module the server is run as, e.g.
            ``"mcp_servers.crm.server"``.
    """
    return f"{shlex.quote(sys.executable)} -m {module}"


class WildcardCorsOrigin(ValueError):
    """Raised when ``OPSPILOT_CORS_ORIGINS`` contains a ``*``.

    A ``ValueError`` because that is what pydantic wraps a ``field_validator``
    exception in anyway; naming it keeps the ``try``/``except`` in an operator's
    face readable. The message lives on the class for the same reason
    ``MissingOperatorToken``'s does: it should read as part of the failure a
    deployment prints, and it must say what to do instead rather than only what
    went wrong.
    """

    def __init__(self) -> None:
        super().__init__(
            "OPSPILOT_CORS_ORIGINS cannot contain '*'. Name the origins explicitly "
            "(e.g. http://localhost:3000); a wildcard lets any page the operator "
            "visits read every API response, and this API can move money. To "
            "disable cross-origin browser access entirely, set it empty -- which is "
            "not the same as allowing everything."
        )


class MissingEmbeddingModel(ValueError):
    """``EMBEDDING_PROVIDER=openai`` was selected with a blank ``EMBEDDING_MODEL``.

    A ``ValueError`` for the reason :class:`WildcardCorsOrigin` is one: that is
    what pydantic wraps a validator exception in, and naming the failure keeps
    the deployment's own output readable. It is also the same shape as
    ``embeddings.UnknownEmbeddingProvider`` -- the other configuration fault in
    this subsystem, which is refused the same way.

    The message names the variable and the fix, because the reader is an
    operator looking at a process that refused to start, not a developer with
    the source open.
    """

    def __init__(self) -> None:
        super().__init__(
            "EMBEDDING_PROVIDER=openai needs EMBEDDING_MODEL to name the model to "
            "call (e.g. text-embedding-3-small). It is blank because the default "
            "provider is local, and the local embedder has no model to name. Set "
            "EMBEDDING_MODEL, or leave EMBEDDING_PROVIDER=local."
        )


class Settings(BaseSettings):
    """Typed view over the environment.

    A flat structure, one field per environment variable, because that is what
    ``.env.example`` documents and what an operator debugging a deployment
    expects to grep for. Grouping the fields into nested models would rename the
    variables (``DATABASE__URL``) and break that correspondence.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Database ------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot",
        alias="DATABASE_URL",
    )

    # -- Authentication ------------------------------------------------------
    # Required. The API refuses to start without it rather than defaulting to
    # open, because a default-open auth is worse than no auth -- it looks like
    # auth.
    opspilot_operator_token: str = Field(default="", alias="OPSPILOT_OPERATOR_TOKEN")

    # -- Browser access (CORS) -----------------------------------------------
    # Comma-separated origins permitted to *read* API responses from a browser.
    # The only origin named by default is the M7 dashboard in local dev, which
    # is the one cross-origin caller this project has.
    #
    # This setting names WHO MAY READ, never who may call: an origin absent
    # from this list can still *send* a request to a reachable port 8000, and
    # CORS is a browser policy with no effect on curl. The bearer token is what
    # authorises anything; this is the narrower question of which pages the
    # browser will hand a response to. See the validator for the one value
    # refused outright, and ``docs/api-contract.md`` §8 for the whole argument.
    #
    # Empty means "no cross-origin browser access at all" -- the app still
    # builds, the middleware installs with an empty allowlist, and no page on
    # another origin can read anything. It does NOT mean "allow everything".
    opspilot_cors_origins: str = Field(
        default="http://localhost:3000", alias="OPSPILOT_CORS_ORIGINS"
    )

    # -- Model provider ------------------------------------------------------
    model_provider: ModelProviderName = Field(default="fake", alias="MODEL_PROVIDER")
    model_name: str = Field(default="", alias="MODEL_NAME")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # The base URL the OpenAI adapter points its SDK at. Empty means the SDK's
    # own default (``https://api.openai.com/v1``), which is the right value for a
    # plain OpenAI deployment -- and the reason the adapter passes it only when
    # non-empty: ``base_url=""`` is not the same as omitting it, it is an invalid
    # endpoint that breaks the default case.
    #
    # Set it to reach any server that speaks the OpenAI HTTP protocol: a
    # self-hosted vLLM, an Azure OpenAI deployment, Together, Groq, or OpenRouter
    # (``https://openrouter.ai/api/v1``). Those are legitimate OpenAI-compatible
    # endpoints, not a special case for one vendor's test run.
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")

    # The base URL the Anthropic adapter points its SDK at. Empty means the SDK's
    # own default (``https://api.anthropic.com``).
    #
    # Unlike ``OPENAI_BASE_URL`` this is *not* the only way to reach the SDK's
    # base-url knob: ``anthropic`` 1.x already reads ``ANTHROPIC_BASE_URL`` from
    # the environment itself, with the precedence kwarg > env > default. So a
    # deployment that sets the variable need not go through this field at all --
    # this setting exists so the worker's wiring names the value explicitly and
    # does not depend on an SDK's private precedence rule (and so the two
    # providers read the same way in ``build_worker_provider``).
    #
    # Set it to reach any server that speaks the Anthropic Messages protocol: a
    # self-hosted gateway, a proxy, or a local inference server (e.g. one
    # speaking the API at ``http://127.0.0.1:15742``). The adapter passes it to
    # the SDK only when non-empty; passing ``""`` would be an invalid endpoint.
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")

    # Which mechanism the Anthropic adapter uses to obtain structured output.
    #
    # ``output_config`` (the default) is the correct request for real Anthropic:
    # it constrains generation so a malformed reply is *impossible*. Some
    # Anthropic-protocol gateways accept ``output_config`` and silently ignore it
    # -- they then answer with prose and the reply fails validation. For those,
    # ``tool`` selects the forced-tool-use path: declare one tool whose
    # ``input_schema`` is the target schema and force it with ``tool_choice``, so
    # the model's arguments arrive as an already-structured object with no text
    # to parse. That path makes the same class of failure merely *unlikely*
    # rather than impossible, which is a different failure mode -- so the
    # operator has to opt into it by name and is never defaulted into it.
    #
    # Default ``output_config`` keeps a real Anthropic deployment unchanged; the
    # name says which *request is sent*, because that is the thing that differs.
    anthropic_structured_output: Literal["output_config", "tool"] = Field(
        default="output_config", alias="ANTHROPIC_STRUCTURED_OUTPUT"
    )

    model_timeout_seconds: float = Field(default=60.0, alias="MODEL_TIMEOUT_SECONDS")

    # Whether structured requests go out asking for ``json_object`` instead of
    # the ``json_schema`` envelope.
    #
    # Some OpenAI-compatible endpoints accept the ``json_schema`` envelope,
    # answer HTTP 200, and then ignore the schema. Measured against MiniMax with
    # the eval's own ``TicketClassification``: one reply was valid JSON under
    # keys the model had invented, the next was Markdown prose, so 0 of 20
    # classification cases could run. The *same* endpoint honours
    # ``response_format={"type": "json_object"}``, which only asks for JSON
    # without constraining its fields -- and with the schema described in the
    # prompt it then returned exactly the keys the model was asked for.
    #
    # So on, the OpenAI adapter's first and only request asks for ``json_object``
    # with the schema rendered into the system prompt. It does not ask again
    # after a bad reply: the prose reply was the case that made retrying useless,
    # since a fallback gated on "the answer was JSON" never fired for it. Off is
    # the default because real OpenAI constrains generation and needs none of
    # this, and every deployment should get the request it sent before.
    #
    # Validation is unchanged either way: the reply still has to validate against
    # the same Pydantic model. This widens what the endpoint is *asked*, never
    # what is accepted.
    #
    # The failure is named, not the vendor: this is the defect
    # ``adapters/models/openai_provider.py``'s ``reasoning_content`` fallback
    # was measured on, not a special case for one provider.
    openai_json_object_fallback: bool = Field(default=False, alias="OPENAI_JSON_OBJECT_FALLBACK")

    # Which recorded fixture the ``fake`` provider replays, by scenario name.
    #
    # The ``fake`` provider answers a call one of two ways: from a named
    # scenario's script, or by matching the request hash against a fixture's
    # recorded hash. Every ``request_hash`` in every shipped fixture is null, so
    # the hash path has no data to match -- and the scenario path had no caller,
    # because only the test harness passes ``scenario``. The result was that
    # ``MODEL_PROVIDER=fake``, which ``.env.example`` describes as the mode a
    # fresh clone runs in, could not complete a single model call from a real
    # process: a run died at ``classifying`` with ``UnmatchedFixtureError``.
    #
    # This setting is what connects the two. It is the *whole* difference
    # between "the demo works" and "the demo works only inside pytest".
    #
    # Empty means no scenario, which leaves the hash path in charge -- correct
    # for a fixture that recorded real hashes, and the honest default for one
    # that did not, because the run then fails with a message naming the request
    # hash rather than silently replaying an unrelated script.
    #
    # Known limitation, stated rather than discovered later: one process replays
    # one scenario. Every ticket a worker handles is answered from the same
    # script, so a demo that submits a duplicate-charge ticket and then an
    # already-refunded one cannot serve both from one worker. See
    # ``docs/limitations.md``.
    fake_scenario: str = Field(default="", alias="OPSPILOT_FAKE_SCENARIO")

    # -- Embeddings ----------------------------------------------------------
    embedding_provider: EmbeddingProviderName = Field(default="local", alias="EMBEDDING_PROVIDER")
    # The model to call, and consulted only when the provider is ``openai``.
    # ``build_embedder`` discards it under ``local``: that embedder is a keyed
    # blake2b hash over the text's stemmed terms, so it has no model behind it
    # and no name that could describe one.
    #
    # Empty is the default because there is no truthful value to put here for
    # the default provider. It used to be OpenAI's ``text-embedding-3-small``,
    # which a default deployment never called -- and an eval recorded
    # ``embedding_provider: "local"`` beside
    # ``embedding_model: "text-embedding-3-small"``, so the run's retrieval was
    # attributed to a model that was never asked for anything. That is the
    # defect ``docs/limitations.md`` §8 describes: a claim that reads as
    # evidence of something it is not. Unset says what actually happened.
    #
    # Set it only for ``EMBEDDING_PROVIDER=openai``, where it becomes the
    # ``model`` argument of the embeddings request. Blank there is refused at
    # startup rather than sent as ``model=""``, which fails provider-side with
    # an error that says nothing about which variable is missing.
    embedding_model: str = Field(default="", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1536, alias="EMBEDDING_DIM")

    # -- Retrieval -----------------------------------------------------------
    retrieval_top_k: int = Field(default=5, alias="RETRIEVAL_TOP_K")
    # The abstention threshold, and its default depends on which embedder is
    # configured. The two defaults below are *not* interchangeable: a score from
    # the local lexical embedder and a cosine from a provider embedder are
    # different quantities on different scales.
    #
    #   - ``local`` (default): measured over ``evals/datasets/retrieval.jsonl``
    #     the answerable cases score >= 0.2500 and the ``expect_abstention``
    #     cases <= 0.1844, so any value in (0.1844, 0.2500) separates them.
    #     0.22 sits in that window with margin on both sides. The threshold is
    #     therefore *doing something*: raising it to 0.25 starts abstaining on
    #     answerable questions, and lowering it to 0.18 stops abstaining on
    #     unanswerable ones.
    #   - ``openai``: provider embeddings are near-unit cosine on a familiar
    #     corpus, so 0.35 is the conservative default. It is a different number
    #     for the same setting, and an operator switching providers should
    #     recalibrate rather than inherit this one.
    #
    # Nothing here calibrates a *real* embedder for the operator's own corpus.
    # See ``docs/limitations.md`` §3.
    retrieval_min_score: float = Field(default=0.22, alias="RETRIEVAL_MIN_SCORE")

    # -- Agent runtime -------------------------------------------------------
    max_steps: int = Field(default=24, alias="MAX_STEPS")

    # -- Worker --------------------------------------------------------------
    worker_poll_interval: float = Field(default=1.0, alias="WORKER_POLL_INTERVAL")
    worker_id: str = Field(default="", alias="WORKER_ID")

    # The claim lease: how long a claimed run stays out of every other worker's
    # hands before the lease expires and the run becomes claimable again. It is
    # the mechanism that lets two workers share one database (docs/adr/0005):
    # ``claim_next`` takes a row only when its lease is absent or expired and
    # sets the lease forward in the same transaction, so a *live* worker's run
    # is not handed to a second worker, while a run whose worker *died* becomes
    # claimable once the lease runs out (``docs/limitations.md`` §2, C1).
    #
    # The default is justified against a measurement, not picked round. A live
    # eval measured a run's p95 drive duration at **276 seconds** (the figure
    # ``limitations.md`` §2 and the p95 note in ``docs/progress.md``'s latency
    # work carry). A default of **300 seconds** therefore exceeds a p95 run with
    # no renewal at all: for the common case the lease is longer than the work,
    # and renewal (below) is a safety net for the tail rather than load-bearing
    # for every run. Choosing the lease to *also* cover the tail would mean a
    # lease of many minutes, and the cost of an over-long lease is worse than the
    # cost of renewal: a worker that dies strands its run for the whole lease
    # before anyone may recover it. A lease somewhat above the p95 keeps that
    # recovery window bounded while renewal keeps a genuinely long run alive.
    #
    # The worker renews at lease/3 (``worker/loop.py``), so a run survives a
    # single missed renewal and any run shorter than the lease needs none. When a
    # run outlives an *unrenewed* lease -- the worker stalled past the lease
    # without the renewer running -- the lease expires and another worker claims
    # the same run; that failure mode is pinned by a test rather than assumed
    # away (``tests/integration/test_worker_claim_lease.py``).
    worker_claim_lease_seconds: float = Field(default=300.0, alias="WORKER_CLAIM_LEASE_SECONDS")

    # -- MCP servers ---------------------------------------------------------
    # Which transport the tool gateway dispatches over.
    #
    #   inprocess (default) -- the servers are constructed inside the API/worker
    #       process and called directly. Hermetic and fast; it is what every test
    #       in the suite exercises, and it covers registration, JSON Schema
    #       generation, structured output and the CallToolResult envelope.
    #
    #   stdio -- the worker spawns each server as a real child process and calls
    #       it over a pipe, using the three commands below. This is the only path
    #       that exercises the transport itself (stdio framing, the subprocess
    #       handshake, wire serialisation), so it is what an operator selects to
    #       make the transport the thing under test.
    #
    # In-process is the default rather than stdio for three reasons, all recorded
    # in adapters/tools/mcp_gateway.py: flipping the default would change what the
    # existing test suite exercises; a subprocess server owns its own on-disk store
    # and would leak state between tests without a private OPSPILOT_MCP_DATA_DIR;
    # and each spawn is an interpreter boot plus a handshake.
    mcp_transport: Literal["inprocess", "stdio"] = Field(default="inprocess", alias="MCP_TRANSPORT")

    # The command the gateway spawns for each server, when ``MCP_TRANSPORT=stdio``.
    #
    # A full command *line*: the executable and its arguments, separated by
    # spaces, with shell quoting available for a path containing spaces
    # (``"C:\Program Files\Python\python.exe" -m mcp_servers.crm.server``). It is
    # split with shlex into an explicit argv list and spawned with no shell, so
    # shell metacharacters are literal arguments rather than a second command --
    # a ``.env`` value must never be able to execute anything but the server it
    # names. Arguments are supported and are why this is not a bare ``split()``:
    # every server takes ``--reset``, which restores its store from the committed
    # seed before serving.
    #
    # Only consulted under ``MCP_TRANSPORT=stdio``; under the default they are
    # inert, because nothing is spawned. See .env.example.
    mcp_crm_command: str = Field(
        default_factory=lambda: default_mcp_server_command("mcp_servers.crm.server"),
        alias="MCP_CRM_COMMAND",
    )
    mcp_billing_command: str = Field(
        default_factory=lambda: default_mcp_server_command("mcp_servers.billing.server"),
        alias="MCP_BILLING_COMMAND",
    )
    mcp_issues_command: str = Field(
        default_factory=lambda: default_mcp_server_command("mcp_servers.issues.server"),
        alias="MCP_ISSUES_COMMAND",
    )

    # -- Tool policy ---------------------------------------------------------
    # Comma-separated tool names to refuse. May only remove capability.
    opspilot_tool_denylist: str = Field(default="", alias="OPSPILOT_TOOL_DENYLIST")
    # The largest refund the policy engine will let reach the approval gate,
    # minor-unit free (it is compared as a Decimal against the call's ``amount``).
    # Empty means "no ceiling": the approval gate is still the control that stops
    # the money (``docs/risks.md`` R3) -- the ceiling exists to make an
    # obviously-too-large number a *rejection* rather than something a tired
    # approver has to catch. This removes capability only; it can never widen a
    # permission, which is static code (ADR-0003).
    refund_ceiling: str = Field(default="", alias="OPSPILOT_REFUND_CEILING")

    # -- Observability -------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    store_full_prompts: bool = Field(default=True, alias="STORE_FULL_PROMPTS")

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        """Normalise the log level so ``log_level=debug`` behaves as ``DEBUG``."""
        return value.upper()

    @field_validator("opspilot_cors_origins")
    @classmethod
    def _refuse_wildcard_cors_origin(cls, value: str) -> str:
        """Refuse to parse a wildcarded ``OPSPILOT_CORS_ORIGINS``.

        Enforced in configuration rather than only in the middleware, so the
        wildcard cannot reach the app even if a later edit builds
        ``allow_origins`` by a different route than the one this repo has today.
        Two notes on why this is fatal and not merely warned about:

        - ``["*"]`` on this API means *any* page the operator visits while the
          API is running may read every response, including the approval queue
          and the run timeline. The endpoints can move money (§5), so a wildcard
          is not a lax default, it is the whole surface.
        - ``allow_credentials=True`` with ``["*"]`` is additionally rejected by
          Starlette, so the wildcard would in practice mean silently losing the
          credentials header -- a broken dashboard plus a wide-open read path.

        Refusing to construct the settings is the honest response: an operator
        who typed ``*`` meant "let anything in", and silently widening the API to
        that is worse than a startup error that names the fix. This is the same
        rule as the operator token -- config may remove capability, never grant
        it.
        """
        entries = [item.strip() for item in value.split(",") if item.strip()]
        if "*" in entries:
            raise WildcardCorsOrigin
        return value

    @model_validator(mode="after")
    def _require_an_embedding_model_for_the_openai_provider(self) -> Settings:
        """Refuse ``EMBEDDING_PROVIDER=openai`` without a model to call.

        Deliberately asymmetric rather than blanket: a blank ``EMBEDDING_MODEL``
        is *correct* under ``local``, which is the default and is what a fresh
        clone and CI run on. Refusing it there would make the shipped default
        unusable, so the rule is stated as the pair it actually is.

        Enforced here because this is the earliest point at which both settings
        are known, and because the empty default made this the likely shape of a
        real mistake: an operator who switched ``EMBEDDING_PROVIDER`` and not
        this variable. Without the check that deployment comes up healthy and
        fails on its first retrieval, sending ``model=""`` to the API and
        surfacing the provider's rejection of an empty string -- which reads as
        a key, quota or connectivity problem, and is none of those. A worker
        that refuses to start and names the variable is the cheaper failure
        (``docs/mcp-contracts.md``).
        """
        if self.embedding_provider == "openai" and not self.embedding_model.strip():
            raise MissingEmbeddingModel
        return self

    @property
    def tool_denylist(self) -> frozenset[str]:
        """Parse ``OPSPILOT_TOOL_DENYLIST`` into a set of tool names.

        Whitespace and empty entries are dropped. There is no value of this
        setting that widens a permission: it can only remove a tool.
        """
        return frozenset(
            name.strip() for name in self.opspilot_tool_denylist.split(",") if name.strip()
        )

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """Parse ``OPSPILOT_CORS_ORIGINS`` into the exact origins to allow.

        Whitespace and empty entries are dropped, and the result is a tuple in
        the operator's declared order so the effective policy reads back the way
        it was written. A bare ``*`` cannot appear here: the field validator
        above refuses to construct settings containing one, so a wildcard is not
        reachable from this accessor even by a caller that ignores the raw field.

        An empty tuple is a meaningful value, not a failure: it means no
        cross-origin page may read any response, which is the correct posture for
        a deployment where the dashboard is served from the same origin as the
        API or is not used at all.
        """
        return tuple(
            origin.strip() for origin in self.opspilot_cors_origins.split(",") if origin.strip()
        )

    @property
    def is_sqlite(self) -> bool:
        """Whether the configured database URL targets SQLite.

        Adapters branch on this for the documented Postgres/SQLite differences
        (no ``FOR UPDATE SKIP LOCKED``, no pgvector -- see ``docs/data-model.md``
        §6 and ADR-0004).
        """
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructed once and cached.

    Caching is deliberate: settings are read from the environment at import
    time, so every caller that asks gets the same object. Tests override it by
    clearing the cache (``get_settings.cache_clear()``) after monkeypatching the
    environment.
    """
    return Settings()
