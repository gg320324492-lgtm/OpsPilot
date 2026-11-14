"""Bearer-token authentication dependency.

Responsibility: the single FastAPI dependency that guards every route
**except the two health probes**. It reads the
``Authorization: Bearer <token>`` header and compares it to
``OPSPILOT_OPERATOR_TOKEN`` using ``secrets.compare_digest``, so the comparison
is constant-time and the token cannot be recovered by timing a byte-at-a-time
guess.

The exemptions are ``/health`` and ``/ready``, and they are exempt on purpose:
a liveness probe carries no credential, so requiring one would mean the probe
reports on the credential rather than on the service. Everything that reads or
writes the deployment's data is behind this dependency.

Layer: ``api``.

Phase 1 is deliberately one operator token, not RBAC/SSO (see
``docs/architecture.md`` §13): a role table with one role is a belief, not a
design. SSO is Phase 3. The token is required -- there is no default.

Two rules from ``docs/api-contract.md`` §8, both load-bearing:

- The comparison is ``secrets.compare_digest``, not ``==``, because ``==``
  short-circuits on the first differing byte and leaks the token prefix through
  response timing.
- An unset token makes the app **refuse to start** (``MissingOperatorToken``,
  raised by :func:`ensure_operator_token_configured` at startup). A per-request
  401 would be a defence, but a process that came up silently open once is a
  process an operator trusts when they should not.
- A token **shorter than** ``MIN_TOKEN_LENGTH`` also refuses to start
  (``WeakOperatorToken``). A credential that fits in a hand-enumerable space is
  not a credential on an interface that answers refunds.
"""

from __future__ import annotations

import secrets
from typing import Final

from fastapi import Header, HTTPException, status

from opspilot.settings import Settings, get_settings


class MissingOperatorToken(RuntimeError):
    """Raised at startup when ``OPSPILOT_OPERATOR_TOKEN`` is unset or empty.

    A ``RuntimeError`` rather than an ``OpsPilotError``: this is a
    *configuration* fault detected while building the process, not a domain
    condition, and it must escape the app factory so uvicorn exits non-zero
    rather than serving an unauthenticated surface.

    The message lives on the class (rather than at the raise site) so the
    explanation -- why a default-open auth is worse than no auth -- reads as part
    of the failure a deployment prints, not as a stray string.
    """

    def __init__(self) -> None:
        super().__init__(
            "OPSPILOT_OPERATOR_TOKEN is not set. The API refuses to start without it: "
            "a default-open auth is worse than no auth because it looks like auth. "
            "Set OPSPILOT_OPERATOR_TOKEN in the environment (see .env.example)."
        )


class WeakOperatorToken(RuntimeError):
    """Raised at startup when ``OPSPILOT_OPERATOR_TOKEN`` is set but too short.

    Same family as :class:`MissingOperatorToken` -- a configuration fault that
    must escape the app factory so a deployment with a guessable credential is
    told to fix it rather than served. A one- or two-character token passes a
    non-empty check and fails ``compare_digest`` only by being wrong; against an
    unauthenticated network interface it is a credential an attacker can
    enumerate in a handful of requests, and the endpoints behind it approve
    refunds. The floor is documented in ``.env.example`` and enforced nowhere
    else, so it lives here.
    """

    def __init__(self, actual_length: int) -> None:
        super().__init__(
            f"OPSPILOT_OPERATOR_TOKEN is {actual_length} character(s), below the "
            f"minimum of {MIN_TOKEN_LENGTH}. A short token is enumerable on an "
            "unauthenticated interface. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))" '
            "(see .env.example)."
        )


#: The shortest token ``ensure_operator_token_configured`` accepts. Not a
#: strength guarantee -- entropy is the operator's choice -- but a refusal of
#: values whose brute-force space is small enough to enumerate by hand. Every
#: shipped generator (``secrets.token_urlsafe(32)``) and every test value in
#: this repository clears it; the two-character placeholder CI once used does
#: not, which is the point.
MIN_TOKEN_LENGTH: Final[int] = 16


def ensure_operator_token_configured(settings: Settings | None = None) -> str:
    """Return the configured token, or raise if it is empty or too short.

    Called once by the app factory, before any route can be served. Failing here
    is the whole point: a default-open auth is worse than no auth because it
    looks like auth, and a two-character auth looks like auth too.

    Args:
        settings: The settings to read; defaults to the process-wide settings.

    Returns:
        The non-empty operator token, at least ``MIN_TOKEN_LENGTH`` characters.

    Raises:
        MissingOperatorToken: If ``opspilot_operator_token`` is empty.
        WeakOperatorToken: If it is shorter than ``MIN_TOKEN_LENGTH``.
    """
    resolved = settings if settings is not None else get_settings()
    token = resolved.opspilot_operator_token
    if not token:
        raise MissingOperatorToken
    if len(token) < MIN_TOKEN_LENGTH:
        raise WeakOperatorToken(len(token))
    return token


def _extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header value."""
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return None
    return credentials.strip()


async def require_operator(authorization: str | None = Header(default=None)) -> str:
    """Validate the bearer token and return the operator id.

    The dependency every guarded route declares. It reads the header itself (via
    ``Header``), so a router only needs ``Depends(require_operator)``.

    Args:
        authorization: The raw ``Authorization`` header value, injected by
            FastAPI.

    Returns:
        The operator identity -- ``"operator"`` in Phase 1, where there is one
        token and therefore one role.

    Raises:
        HTTPException: 401 if the header is missing, malformed, or the token does
            not match. ``secrets.compare_digest`` is used for the comparison so
            the check is constant-time.
    """
    token = ensure_operator_token_configured()
    presented = _extract_bearer(authorization)
    if presented is None or not secrets.compare_digest(presented, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "operator"
