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

from pydantic import Field, field_validator
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
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
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
