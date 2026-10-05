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

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelProviderName = Literal["fake", "anthropic", "openai"]
EmbeddingProviderName = Literal["local", "openai"]


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

    # -- Model provider ------------------------------------------------------
    model_provider: ModelProviderName = Field(default="fake", alias="MODEL_PROVIDER")
    model_name: str = Field(default="", alias="MODEL_NAME")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model_timeout_seconds: float = Field(default=60.0, alias="MODEL_TIMEOUT_SECONDS")

    # -- Embeddings ----------------------------------------------------------
    embedding_provider: EmbeddingProviderName = Field(default="local", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1536, alias="EMBEDDING_DIM")

    # -- Retrieval -----------------------------------------------------------
    retrieval_top_k: int = Field(default=5, alias="RETRIEVAL_TOP_K")
    retrieval_min_score: float = Field(default=0.35, alias="RETRIEVAL_MIN_SCORE")

    # -- Agent runtime -------------------------------------------------------
    max_steps: int = Field(default=24, alias="MAX_STEPS")

    # -- Worker --------------------------------------------------------------
    worker_poll_interval: float = Field(default=1.0, alias="WORKER_POLL_INTERVAL")
    worker_id: str = Field(default="", alias="WORKER_ID")

    # -- MCP servers ---------------------------------------------------------
    mcp_crm_command: str = Field(
        default="python -m mcp_servers.crm.server", alias="MCP_CRM_COMMAND"
    )
    mcp_billing_command: str = Field(
        default="python -m mcp_servers.billing.server", alias="MCP_BILLING_COMMAND"
    )
    mcp_issues_command: str = Field(
        default="python -m mcp_servers.issues.server", alias="MCP_ISSUES_COMMAND"
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
