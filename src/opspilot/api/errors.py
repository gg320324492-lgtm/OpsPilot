"""Exception handlers producing OpsPilot's single error shape.

Responsibility: install handlers that convert domain exceptions and unhandled
errors into one JSON envelope, so a client parses one shape for every failure
rather than guessing per endpoint.

Layer: ``api``.

Mapping: ``NotFoundError`` -> 404, ``IllegalTransition``/``PolicyDenied`` -> 409,
``UnknownTool`` -> 422, ``ApprovalArgumentsChanged`` -> 409, auth failure -> 401.
``RunParked`` is *not* an error here -- it surfaces as a 202/``waiting_approval``
run status, because parking is the workflow working.

The HTTP status is carried by ``code`` (``docs/api-contract.md`` §6). Clients
branch on ``code`` and never parse ``message``, so the message is free to become
more helpful without becoming a compatibility surface.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from opspilot.api.schemas import ErrorBody, ErrorResponse
from opspilot.domain.errors import (
    ApprovalArgumentsChanged,
    IllegalTransition,
    NotFoundError,
    PolicyDenied,
    UnknownTool,
)

_logger = logging.getLogger("opspilot.api")

# The stable machine identifier -> HTTP status mapping from contract §6. The
# codes are the contract; the numbers are how the contract is served. Keeping
# the pair in one table means a new code cannot be added with a made-up status.
_CODE_STATUS: dict[str, int] = {
    "validation_error": 400,
    "ticket_not_found": 404,
    "run_not_found": 404,
    "approval_not_found": 404,
    "approval_already_decided": 409,
    "run_not_awaiting_approval": 409,
    "run_already_completed": 409,
    "ticket_exists": 409,
    "schema_invalid": 422,
    "internal_error": 500,
    "not_ready": 503,
}


class ApiError(HTTPException):
    """An HTTP failure carrying the contract's ``code`` alongside its status.

    Routers raise this rather than a bare ``HTTPException`` so that a 404 is
    ``ticket_not_found`` / ``run_not_found`` / ``approval_not_found`` -- the code
    the client branches on -- and not an indistinguishable generic 404. A bare
    ``HTTPException`` is still handled (see :func:`_http_exception_handler`) but
    loses the code, which is why none of our own code raises one.
    """

    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, object] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(status_code=status_code, detail=message)


def error_response(
    *, code: str, message: str, details: dict[str, object] | None = None
) -> dict[str, object]:
    """Build the canonical error body (contract §6).

    The parameter is ``details`` but the JSON field is ``details`` too; the
    envelope is built through the Pydantic ``ErrorResponse`` model so the OpenAPI
    schema and the runtime body cannot drift.

    Args:
        code: The stable machine identifier clients branch on.
        message: The human-readable explanation. Never parsed by clients.
        details: Optional structured context (ids, statuses, failing check).

    Returns:
        The ``{"error": {...}}`` envelope as a plain JSON-serializable dict.
    """
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return body.model_dump(mode="json")


def _json_error(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    """Return a ``JSONResponse`` with the error envelope and the given status."""
    return JSONResponse(
        status_code=status_code,
        content=error_response(code=code, message=message, details=details),
    )


def _status_code_for(code: str, *, fallback: int) -> int:
    """Look up the contract status for ``code``, falling back when unknown."""
    return _CODE_STATUS.get(code, fallback)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the handlers to the app.

    Handlers are installed for both ``Exception`` and ``HTTPException``, so no
    path -- a domain failure, a validation failure, a bare 404 for an unknown
    route, or an unhandled bug -- escapes the single envelope.

    Args:
        app: The FastAPI application to install the handlers on.
    """

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return _json_error(
            status_code=exc.status_code, code=exc.code, message=exc.message, details=exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default is `{"detail": [...]}`, which is a *second* error
        # shape. Re-shaping it here is what makes the contract's "one shape,
        # everywhere" true rather than aspirational.
        return _json_error(
            status_code=422,
            code="schema_invalid",
            message="Request body failed validation.",
            details={"errors": _serializable_errors(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Covers the framework's own 404s/405s (e.g. an unknown path) so they,
        # too, speak the envelope. The code is derived from the status because a
        # bare HTTPException carries no code of ours.
        code = _code_for_status(exc.status_code)
        return _json_error(
            status_code=exc.status_code, code=code, message=str(exc.detail), details=None
        )

    @app.exception_handler(NotFoundError)
    async def _not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
        return _json_error(status_code=404, code="not_found", message=str(exc), details=None)

    @app.exception_handler(IllegalTransition)
    async def _illegal_transition_handler(
        _request: Request, exc: IllegalTransition
    ) -> JSONResponse:
        return _json_error(
            status_code=409,
            code="illegal_transition",
            message=str(exc),
            details={"current": exc.current.value, "requested": exc.requested.value},
        )

    @app.exception_handler(ApprovalArgumentsChanged)
    async def _arguments_changed_handler(
        _request: Request, exc: ApprovalArgumentsChanged
    ) -> JSONResponse:
        return _json_error(
            status_code=409,
            code="approval_arguments_changed",
            message=str(exc),
            details=None,
        )

    @app.exception_handler(PolicyDenied)
    async def _policy_denied_handler(_request: Request, exc: PolicyDenied) -> JSONResponse:
        return _json_error(
            status_code=409, code="policy_denied", message=str(exc), details={"reason": exc.reason}
        )

    @app.exception_handler(UnknownTool)
    async def _unknown_tool_handler(_request: Request, exc: UnknownTool) -> JSONResponse:
        return _json_error(status_code=422, code="unknown_tool", message=str(exc), details=None)

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        # The message stays generic: an exception string can carry a connection
        # string, a prompt or a customer's email. The detail goes to the log,
        # keyed by the trace_id returned to the caller so the two can be joined.
        trace_id = uuid4().hex
        _logger.error(
            "unhandled error [trace_id=%s]: %s",
            trace_id,
            exc,
            exc_info=exc,
        )
        return _json_error(
            status_code=500,
            code="internal_error",
            message="An internal error occurred.",
            details={"trace_id": trace_id},
        )


def _serializable_errors(
    errors: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Reduce Pydantic's validation errors to JSON-safe primitives.

    Pydantic's ``errors()`` can embed exception objects and non-serializable
    values in ``ctx``; only ``loc``/``msg``/``type`` are kept, because those are
    the fields a client can act on and the rest risks a serialization failure
    *inside* the error handler.
    """
    cleaned: list[dict[str, object]] = []
    for error in errors:
        location = error.get("loc", ())
        parts = [str(part) for part in location] if isinstance(location, list | tuple) else []
        cleaned.append(
            {
                "loc": parts,
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return cleaned


def _code_for_status(status_code: int) -> str:
    """Best-effort stable code for a framework-raised HTTP status."""
    if status_code == 404:
        return "not_found"
    if status_code == 405:
        return "method_not_allowed"
    if status_code == 401:
        return "unauthorized"
    if status_code == 400:
        return "validation_error"
    return "error"
