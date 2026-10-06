"""Emit the API's OpenAPI schema to a file, without a database or a running server.

Responsibility: produce the *live* OpenAPI document as JSON on stdout (or to a
path given on the command line), so the TypeScript client types are generated
from the app as it actually is rather than from a schema somebody copied into
the repository and then forgot to update.

Layer: tooling. This script imports the app factory and nothing else from the
service; it is not part of the ``api`` layer and nothing in ``src/opspilot``
imports it.

Two properties make it usable as the source for both generation and the drift
guard:

- **No database.** The app is constructed but never served, and the SQLAlchemy
  engine is created lazily, so pointing ``DATABASE_URL`` at a temp SQLite path
  is belt-and-braces rather than a requirement. Nothing here opens a socket.
- **Deterministic.** ``app.openapi()`` is a pure function of the registered
  routes and the Pydantic models, so two runs on two machines produce
  byte-identical JSON. That is what lets the guard compare rather than
  approximate.

The token is set to a throwaway literal because :func:`create_app` refuses to
start without one, not because anything here authenticates. It is never sent
anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# The repository root, so `src/` is importable when this script is run directly
# (`python scripts/openapi_schema.py`) as well as under pytest.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# A non-secret literal, satisfying the app factory's refuse-to-start-without-a-
# token check. Any non-empty value works; the schema does not depend on it.
_SCHEMA_TOKEN = "openapi-schema-placeholder-token"  # noqa: S105 -- not a credential


def build_schema() -> dict[str, Any]:
    """Construct the app without a database and return its OpenAPI document.

    ``DATABASE_URL`` is redirected at a temporary SQLite path so that a
    developer with a real database configured cannot have this script touch it.
    No connection is opened -- the engine is lazy and nothing is served -- but
    the redirection means that even a future eager engine would create a
    throwaway file rather than migrate the developer's own.

    Returns:
        The OpenAPI 3.1 document, as the dict ``app.openapi()`` produces.

    Raises:
        ImportError: If ``opspilot`` is not importable, which means the package
            is not installed in the active environment.
    """
    _ensure_src_on_path()

    previous = dict(os.environ)
    try:
        os.environ["OPSPILOT_OPERATOR_TOKEN"] = _SCHEMA_TOKEN
        # A forward-slash URL, so the path is built with pathlib rather than
        # os.path.join -- which would emit backslashes on Windows and produce a
        # SQLite URL that is not a URL.
        db_path = Path(tempfile.gettempdir()) / "opspilot-openapi-schema.db"
        os.environ["OPSPILOT_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"

        # Settings are cached process-wide; the redirect above has to be visible
        # to whatever `get_settings()` already memoised.
        from opspilot.settings import get_settings

        get_settings.cache_clear()

        from opspilot.api.app import create_app

        app = create_app()
        schema: dict[str, Any] = app.openapi()
        return schema
    finally:
        os.environ.clear()
        os.environ.update(previous)
        _clear_settings_cache()


def write_schema(destination: Path) -> Path:
    """Write the OpenAPI document to ``destination``, creating parent dirs.

    Args:
        destination: The file to write. Written with sorted keys and no
            incidental whitespace so the output is stable across runs and
            diffs cleanly when it changes.

    Returns:
        The path written, for the caller to report or pipe onward.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination


def canonical_schema() -> str:
    """Return the schema as a canonical string, for hashing and comparison.

    Sorted keys and no incidental whitespace, so two schema objects that differ
    only in dict ordering produce the same string. Used by the drift guard to
    decide whether the committed types are still current.
    """
    return json.dumps(build_schema(), sort_keys=True, separators=(",", ":"))


def _ensure_src_on_path() -> None:
    """Put ``src/`` on ``sys.path`` so the package imports without installation.

    A checkout that has been `pip install -e`'d already resolves ``opspilot``
    from the installed path; a checkout that has not should still be able to
    generate types, because the whole point is that generating them is cheap.
    """
    src = str(_REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _clear_settings_cache() -> None:
    """Drop the memoised settings, if the package is importable at all."""
    try:
        from opspilot.settings import get_settings

        get_settings.cache_clear()
    except ImportError:  # pragma: no cover - the failure path above already raised
        pass


def main(argv: list[str] | None = None) -> int:
    """Write the schema to the path given on the command line.

    Args:
        argv: Arguments to parse; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` if the app could not be constructed.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="File to write the schema to. Written to stdout when omitted.",
    )
    args = parser.parse_args(argv)

    try:
        schema = build_schema()
    except Exception as exc:
        sys.stderr.write(f"failed to build the OpenAPI schema: {exc}\n")
        return 1

    payload = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        sys.stderr.write(f"wrote {args.output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
