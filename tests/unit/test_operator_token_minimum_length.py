"""Unit tests for the operator token's minimum-length refusal.

``ensure_operator_token_configured`` used to check only *emptiness*. A
two-character token -- or any token short enough to enumerate by hand -- passed
the non-empty check and guarded a refund-approving API. These tests pin the
floor added in the 2026-11-14 security review: a set-but-too-short token raises
``WeakOperatorToken`` and the app factory refuses to build, exactly as an
absent token does.

The floor is a floor, not a strength policy: entropy remains the operator's
choice, and ``MIN_TOKEN_LENGTH`` exists so a deployment that set ``"t"`` (a
value that appears in this repository's own test suite as a placeholder) fails
at startup instead of serving.
"""

from __future__ import annotations

import pytest

from opspilot.api.auth import (
    MIN_TOKEN_LENGTH,
    MissingOperatorToken,
    WeakOperatorToken,
    ensure_operator_token_configured,
)
from opspilot.settings import Settings


def _settings_with_token(token: str) -> Settings:
    # The alias, not the field name: ``Settings`` does not set
    # ``populate_by_name``, so the constructor takes the env-var spelling --
    # the same convention every other direct ``Settings(...)`` test uses.
    return Settings(OPSPILOT_OPERATOR_TOKEN=token)


def test_an_empty_token_still_raises_missing() -> None:
    with pytest.raises(MissingOperatorToken):
        ensure_operator_token_configured(_settings_with_token(""))


def test_a_token_at_the_floor_is_accepted() -> None:
    token = "a" * MIN_TOKEN_LENGTH
    assert ensure_operator_token_configured(_settings_with_token(token)) == token


def test_a_token_below_the_floor_raises_weak() -> None:
    with pytest.raises(WeakOperatorToken) as caught:
        ensure_operator_token_configured(_settings_with_token("t"))
    assert "minimum" in str(caught.value)


def test_the_error_names_the_variable_and_the_fix() -> None:
    with pytest.raises(WeakOperatorToken) as caught:
        ensure_operator_token_configured(_settings_with_token("x" * 15))
    message = str(caught.value)
    assert "OPSPILOT_OPERATOR_TOKEN" in message
    assert "token_urlsafe" in message


def test_a_realistic_generated_token_clears_the_floor() -> None:
    import secrets

    token = secrets.token_urlsafe(32)
    assert len(token) > MIN_TOKEN_LENGTH
    assert ensure_operator_token_configured(_settings_with_token(token)) == token


def test_the_app_factory_refuses_a_short_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_app`` refuses to build, so uvicorn exits non-zero.

    Same shape as the empty-token case: the refusal is a startup check, not a
    per-request one.
    """
    from opspilot.api.app import create_app

    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "short")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(WeakOperatorToken):
            create_app()
    finally:
        get_settings.cache_clear()
