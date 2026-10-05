"""Exception handlers producing OpsPilot's single error shape.

Responsibility: install handlers that convert domain exceptions and unhandled
errors into one JSON envelope, so a client parses one shape for every failure
rather than guessing per endpoint.

Layer: ``api``.

Mapping: ``NotFoundError`` -> 404, ``IllegalTransition``/``PolicyDenied`` -> 409,
``UnknownTool`` -> 422, ``ApprovalArgumentsChanged`` -> 409, auth failure -> 401.
``RunParked`` is *not* an error here -- it surfaces as a 202/``waiting_approval``
run status, because parking is the workflow working.
"""

from __future__ import annotations


def register_exception_handlers(app: object) -> None:
    """Attach the handlers to the app. M0 stub."""
    raise NotImplementedError


def error_response(
    *, code: str, message: str, detail: dict[str, object] | None = None
) -> dict[str, object]:
    """Build the canonical error body. M0 stub."""
    raise NotImplementedError
