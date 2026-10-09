"""The one place ``LOG_LEVEL`` becomes an actual logging configuration.

Responsibility: apply the verbosity an operator configured to the process's
loggers. ``settings.py`` reads, normalises and validates the value; this module
is what makes it *do* something -- which, until now, nothing did. ``LOG_LEVEL``
was a documented setting with no reader anywhere in the tree, so changing it in
``docker-compose.yml`` changed nothing at all: a knob that turns nothing is worse
than a missing one, because it looks like control.

Layer: leaf / cross-cutting. It imports the standard library and nothing else,
so every layer may import it without a dependency appearing -- and nothing in
the package imports it at module scope. The configuration is applied by
:func:`apply_log_level`, called from the process entry points (``api/__main__.py``
and ``worker/__main__.py``) and from nowhere else.

**Why a function and not module-level configuration.** A library module that
configures logging on import dictates policy to whatever imported it: a test
suite, an unrelated process, an interactive shell. ``pytest``'s ``caplog``
fixture installs its own handler on the root logger precisely so it can read what
is emitted, and a module that replaced or re-levelled that at import time would
break every test that asserts on a log line -- which is most of the logging
assertions this suite has. So the configuration is a *call*, made by the process
that owns the policy. The same shape as ``ensure_operator_token_configured``
being called by the app factory rather than at import.

**Why setting a level alone is not enough.** A logger with no handler falls back
to ``logging.lastResort``: a ``WARNING``-threshold handler on stderr. Enabling
``INFO`` records and then dropping every one of them would be the same defect in
a new place, so :func:`apply_log_level` also installs a stderr handler when the
target logger has none. That is also the stream a container runtime collects:
the worker image sets ``PYTHONUNBUFFERED=1`` and ``docker compose logs`` reads
stderr. Under pytest the root logger already has handlers, so none is added and
no record is emitted twice.

**What this does not cover.** In-process ``alembic`` runs, which call
``fileConfig`` and disable every logger it does not name -- that is
``migrations/env.py``'s business, and the hazard is pinned by
``tests/integration/test_failed_tool_call_visibility.py``. Setting the
application's own logger's level directly (below) is what keeps that class of
reconfiguration from turning the application's records off silently.
"""

from __future__ import annotations

import logging
import sys
import typing

# Time, level and logger name before the message: a container log line has no
# other source to attribute it to, and "which logger said this" is the first
# question of a line read out of ``docker compose logs``.
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# The namespace this application's own records are written under. Setting its
# level explicitly, rather than relying on the root's, is deliberate -- see
# :func:`apply_log_level`.
_APPLICATION_LOGGER = "opspilot"


class UnknownLogLevel(ValueError):
    """``LOG_LEVEL`` names a level the standard library does not define.

    A ``ValueError`` because that is what ``logging`` itself raises for a bad
    level, so a caller catching one catches the other. The message lists the
    accepted values rather than only rejecting the typo: an operator reading a
    refused start should be able to fix it without opening the source.

    Raised at start-up rather than falling back to ``INFO``. A typo is a
    configuration error, and a silent fallback is how ``LOG_LEVEL=INFOO`` turns
    into "the setting I changed did nothing" -- the exact defect this module
    exists to fix.
    """

    def __init__(self, value: str) -> None:
        known = ", ".join(sorted(logging.getLevelNamesMapping()))
        super().__init__(f"LOG_LEVEL={value!r} is not a logging level; use one of: {known}")


def _resolve_level(level: str) -> int:
    """The numeric level ``level`` names, or raise :class:`UnknownLogLevel`.

    Case-insensitive and whitespace-tolerant because ``settings`` upper-cases
    ``LOG_LEVEL`` already and a doubled space in a compose file is not worth a
    refused start -- the value is normalised at the point of use rather than
    assumed to have been normalised on the way in.
    """
    resolved = logging.getLevelNamesMapping().get(level.strip().upper())
    if resolved is None:
        raise UnknownLogLevel(level)
    return resolved


def apply_log_level(level: str, *, logger: logging.Logger | None = None) -> int:
    """Apply ``level`` to ``logger``, adding a stderr handler if it has none.

    Args:
        level: The level name, case-insensitive (``"debug"`` is ``"DEBUG"``).
        logger: The logger to configure. Defaults to the process root, which is
            what an entry point wants: every ``opspilot.*`` logger is ``NOTSET``
            and inherits from it. Passing one scopes the change to a subtree, so
            a caller with its own namespace -- or a test that must not touch the
            root for the rest of the session -- can configure exactly that.

    Returns:
        The numeric level applied, so a caller (or a test) can assert what the
        setting became rather than re-deriving it from the name.

    Raises:
        UnknownLogLevel: If ``level`` names no level the stdlib knows.
    """
    target = logging.getLogger() if logger is None else logger
    resolved = _resolve_level(level)

    if not target.handlers:
        target.addHandler(_stderr_handler())
    target.setLevel(resolved)
    if logger is None:
        # The application's own namespace, set directly rather than inherited.
        # The root logger is shared with every framework in the process, and
        # ``alembic``'s ``fileConfig`` and ``uvicorn``'s ``dictConfig`` both
        # reconfigure it in-process -- the first of those disables every logger
        # it does not name by default. Setting ``opspilot`` explicitly is what
        # keeps a framework's setup from turning this application's records off.
        logging.getLogger(_APPLICATION_LOGGER).setLevel(resolved)
    return resolved


def _stderr_handler() -> logging.StreamHandler[typing.TextIO]:
    """A handler on stderr -- the stream a container runtime collects."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


__all__ = ["UnknownLogLevel", "apply_log_level"]
