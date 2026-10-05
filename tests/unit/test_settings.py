"""Unit tests for settings loading and the dialect switch."""

from __future__ import annotations

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
