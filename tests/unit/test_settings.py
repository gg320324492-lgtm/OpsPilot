"""Unit tests for settings loading and the dialect switch."""

from __future__ import annotations

import pytest

from opspilot.settings import Settings


def test_sqlite_url_sets_is_sqlite() -> None:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    assert settings.is_sqlite is True


def test_postgres_url_does_not_set_is_sqlite() -> None:
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"
    )
    assert settings.is_sqlite is False


def test_embedding_dim_defaults_to_1536() -> None:
    assert Settings().embedding_dim == 1536


def test_embedding_dim_is_configurable() -> None:
    settings = Settings(EMBEDDING_DIM=768)
    assert settings.embedding_dim == 768


def test_log_level_is_upper_cased() -> None:
    assert Settings(LOG_LEVEL="debug").log_level == "DEBUG"


def test_tool_denylist_parses_and_ignores_blanks() -> None:
    settings = Settings(OPSPILOT_TOOL_DENYLIST=" billing.issue_refund , , crm.get_customer ")
    assert settings.tool_denylist == frozenset({"billing.issue_refund", "crm.get_customer"})


def test_tool_denylist_empty_by_default() -> None:
    assert Settings().tool_denylist == frozenset()


# -- OPSPILOT_CORS_ORIGINS ----------------------------------------------------
# The parsing rules that decide which browsers may read this API. The wildcard
# refusal is tested here as a settings-level fact and again end-to-end in
# tests/integration/test_api_cors.py; the duplication is the point, because a
# guard that only exists at one layer is one refactor from not existing.


def test_cors_origins_default_to_the_local_dashboard() -> None:
    """The default is a specific origin, not a wildcard and not empty.

    A wildcard default would be a hole in an API that can move money, and an
    empty default would mean a fresh clone cannot run the dashboard it ships
    with. One named local origin is the only default that is both safe and
    immediately useful.
    """
    assert Settings().cors_origins == ("http://localhost:3000",)


def test_cors_origins_parses_a_comma_separated_list() -> None:
    settings = Settings(OPSPILOT_CORS_ORIGINS="http://localhost:3000, https://ops.internal.example")
    assert settings.cors_origins == ("http://localhost:3000", "https://ops.internal.example")


def test_cors_origins_drops_whitespace_and_empty_entries() -> None:
    """Stray commas and spaces are noise, not origins."""
    settings = Settings(OPSPILOT_CORS_ORIGINS=" http://localhost:3000 , , http://127.0.0.1:3000 ")
    assert settings.cors_origins == ("http://localhost:3000", "http://127.0.0.1:3000")


def test_cors_origins_empty_is_closed_not_open() -> None:
    """An empty value yields no allowed origins.

    The case a careless implementation inverts: a truthiness check that falls
    back to "everything" when the operator explicitly configured *nothing* turns
    the closed setting into the open one. Asserted because that inversion is the
    realistic way this setting causes an incident.
    """
    assert Settings(OPSPILOT_CORS_ORIGINS="").cors_origins == ()
    assert Settings(OPSPILOT_CORS_ORIGINS="  ,  ").cors_origins == ()


def test_cors_origins_refuses_a_wildcard() -> None:
    """``*`` cannot be configured at all -- the settings refuse to construct."""
    with pytest.raises(ValueError, match="cannot contain"):
        Settings(OPSPILOT_CORS_ORIGINS="*")


def test_cors_origins_refuses_a_wildcard_alongside_named_origins() -> None:
    """A list that is *mostly* specific is refused too.

    ``http://localhost:3000,*`` is how this mistake is actually written: someone
    adds the wildcard to fix one other origin. A validator matching only a lone
    ``*`` would let it through.
    """
    with pytest.raises(ValueError, match="cannot contain"):
        Settings(OPSPILOT_CORS_ORIGINS="http://localhost:3000,*")


# -- model provider endpoints and structured output ---------------------------


def test_anthropic_base_url_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset ``ANTHROPIC_BASE_URL`` yields the empty string -- "use the SDK's own".

    The variable is removed from the environment and ``.env`` is ignored: this
    machine may have ``ANTHROPIC_BASE_URL`` exported (a gateway run), and the
    point under test is the *default*, not whatever the shell or a developer's
    ``.env`` happens to carry.
    """
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert Settings(_env_file=None).anthropic_base_url == ""  # type: ignore[call-arg]


def test_anthropic_base_url_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, ANTHROPIC_BASE_URL="http://127.0.0.1:15742"
    )
    assert settings.anthropic_base_url == "http://127.0.0.1:15742"


def test_anthropic_structured_output_defaults_to_output_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is the real-Anthropic request; the workaround is opt-in.

    This is asserted because the whole safety of the opt-in rests on the default
    being unchanged: an operator must never be silently switched onto the weaker
    mechanism.
    """
    monkeypatch.delenv("ANTHROPIC_STRUCTURED_OUTPUT", raising=False)
    assert Settings(_env_file=None).anthropic_structured_output == "output_config"  # type: ignore[call-arg]


def test_anthropic_structured_output_accepts_the_tool_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_STRUCTURED_OUTPUT", raising=False)
    settings = Settings(_env_file=None, ANTHROPIC_STRUCTURED_OUTPUT="tool")  # type: ignore[call-arg]
    assert settings.anthropic_structured_output == "tool"


def test_anthropic_structured_output_refuses_an_unknown_value() -> None:
    """A typo is a configuration error, not a silent fallback to the default."""
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_STRUCTURED_OUTPUT="toool")  # type: ignore[arg-type]
