"""Bearer-token authentication dependency.

Responsibility: the single FastAPI dependency that guards every route. It reads
the ``Authorization: Bearer <token>`` header and compares it to
``OPSPILOT_OPERATOR_TOKEN`` using ``secrets.compare_digest``, so the comparison
is constant-time and the token cannot be recovered by timing a byte-at-a-time
guess.

Layer: ``api``.

Phase 1 is deliberately one operator token, not RBAC/SSO (see
``docs/architecture.md`` §13): a role table with one role is a belief, not a
design. SSO is Phase 3. The token is required -- there is no default.
"""

from __future__ import annotations


async def require_operator(authorization: str | None = None) -> str:
    """Validate the bearer token and return the operator id.

    Raises an ``HTTPException(401)`` on a missing or mismatched token. Uses
    ``secrets.compare_digest`` rather than ``==``. M0 stub.
    """
    raise NotImplementedError
