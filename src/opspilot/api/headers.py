"""Default security response headers for the API.

Responsibility: a small ASGI middleware that sets ``X-Content-Type-Options``,
``X-Frame-Options`` and ``Referrer-Policy`` on every response, and ``Cache-Control:
no-store`` on authenticated API responses.

Layer: ``api``. Installed by :func:`opspilot.api.app.create_app`; no route can
opt out, because a header one endpoint forgets is a header an attacker probes.

Why this exists
---------------

The API's authorisation model is a bearer token, and its responses carry
operator data -- run traces, approval queues, ticket bodies. None of that data
is cacheable, and none of it belongs in a frame or a sniffed content type:

- ``X-Content-Type-Options: nosniff`` stops a browser from re-interpreting a
  response body whose ``Content-Type`` it dislikes. The API serves JSON, but a
  proxy misconfiguration or a future file-serving route should not be one
  header away from script execution.
- ``X-Frame-Options: DENY`` keeps the dashboard's responses out of an
  attacker's iframe. The write routes already refuse a cross-origin POST
  (the API's CORS allowlist plus the dashboard's own origin checks), but a
  clickjacking frame does not need to read the response to do harm -- it needs
  the operator's click to land on a masked button. Denying framing closes that.
- ``Referrer-Policy: no-referrer`` keeps an operator token that arrived as a
  query parameter -- never on this API by contract, but a future route could
  grow one -- or a ticket id out of the ``Referer`` header of links a dashboard
  page renders.
- ``Cache-Control: no-store`` on ``/api/*`` keeps authenticated responses out
  of shared caches and the browser's disk cache. The two health probes stay
  cacheable-neutral: they carry no credential, and a probe that must not be
  cached is a probe a load balancer has opinions about that this code should
  not force.

This is belt-and-braces on top of the bearer token, not a replacement for it:
a header is read by intermediaries and browsers, and the token is read by the
API. Both matter, and only one of them was being sent.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Headers set on every response, authenticated or not. ``X-Frame-Options`` and
#: ``nosniff`` are harmless on the health probes and useful everywhere else, so
#: the split below is only about caching.
_STATIC_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
)

#: Added on paths under ``/api/`` -- the token-guarded, operator-data surface.
#: ``private`` names the audience (one operator's browser); ``no-store`` names
#: the storage (none, anywhere).
_API_CACHE_HEADERS: tuple[tuple[str, str], ...] = (("cache-control", "private, no-store"),)


class SecurityHeadersMiddleware:
    """An ASGI middleware that adds the security headers to every response.

    A raw ASGI class rather than ``BaseHTTPMiddleware`` because the latter
    buffers and re-wraps the response, and the health probes are on the hot
    path of every compose healthcheck and every CI wait loop. The logic is two
    ``if`` statements over the ``http.response.start`` message; there is
    nothing to buffer.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        extra = _API_CACHE_HEADERS if path.startswith("/api/") else ()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in (*_STATIC_SECURITY_HEADERS, *extra):
                    # A handler that set its own value wins: the middleware is a
                    # default, not an override. Lower-cased comparison because
                    # HTTP/2 lower-cases header names on the wire.
                    if name.encode("latin-1") not in existing:
                        headers.append((name.encode("latin-1"), value.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
