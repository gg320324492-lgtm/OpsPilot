"""The dashboard's client types must not drift from the live OpenAPI schema.

`web/src/lib/api/types.ts` is generated from the FastAPI app's own OpenAPI
document, so the failure this guards against is not hypothetical: it is what
happens the moment someone adds a Pydantic field and forgets to regenerate. The
types then disagree with the API, the dashboard renders `undefined`, and nothing
fails -- `tsc --noEmit` still passes, because the types are internally consistent
and merely wrong.

That is the whole reason a generator is not enough on its own. Generation is a
one-time act; this file is what makes the copy stay honest.

Three things are checked:

1. **The generated types match the live schema.** The same command the developer
   runs (`npm run check:types`) is run here, against the committed file.
2. **The one hand-written exception still matches the backend.** The error
   envelope in `web/src/lib/api/error-envelope.ts` is hand-written because the
   backend does not put it in the schema; that is only safe while it is
   checked, which is what this does.
3. **If the backend ever does emit the envelope, the exception is reported.**
   The right end state is that `error-envelope.ts` is deleted. A test that
   merely tolerated the duplication would let it sit there forever.

No database and no running server: the schema comes from constructing the app
in-process, and the types are compared as text. That keeps this cheap enough to
run on every test invocation, which is the condition for a guard being run at
all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "web"
_TYPES_FILE = _WEB_ROOT / "src" / "lib" / "api" / "types.ts"
_GENERATOR = _WEB_ROOT / "scripts" / "generate-types.mjs"


@pytest.mark.skipif(
    not _TYPES_FILE.exists(),
    reason="the dashboard's generated types are absent (web/ not scaffolded)",
)
def test_client_types_match_the_live_openapi_schema() -> None:
    """The committed ``types.ts`` is exactly what the live schema generates.

    Runs the generator in ``--check`` mode rather than reimplementing the
    comparison here: a guard that regenerated the file itself and compared
    against its own logic would be testing that guard's logic, not that the
    committed artefact is current.
    """
    if not _node_is_available():
        pytest.skip("node is not installed; the client-type guard is a Node check")

    # S603: the arguments are module-level constants and a repository-relative
    # path -- nothing an attacker controls. S607: `node` is resolved from PATH
    # deliberately, so the check runs on whatever Node the developer (or CI)
    # actually uses rather than a pinned path that would not exist there.
    result = subprocess.run(  # noqa: S603
        ["node", str(_GENERATOR), "--check"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=_WEB_ROOT,
        # The generator reads the token from the environment; give it a
        # placeholder so a developer's unset token cannot fail the guard for an
        # unrelated reason. The value never leaves the process.
        # `OPSPILOT_PYTHON` points the generator at *this* interpreter rather
        # than the `.venv/Scripts/python.exe` path it falls back to: that path
        # is Windows-only, so on a Linux CI runner (where the package is
        # `pip install -e`-ed and `sys.executable` is the interpreter that can
        # `import opspilot`) the default would not exist and the generator would
        # exit non-zero for an environment reason, not a drift one.
        env={
            **os.environ,
            "OPSPILOT_OPERATOR_TOKEN": "openapi-schema-placeholder-token",
            "OPSPILOT_PYTHON": sys.executable,
        },
        check=False,
    )

    assert result.returncode == 0, (
        "web/src/lib/api/types.ts has drifted from the live OpenAPI schema.\n"
        "Run `npm run generate:types` in web/ and commit the result.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_no_hand_written_api_type_files_remain() -> None:
    """Every type under ``src/lib/api`` is generated, with no exceptions left.

    ``error-envelope.ts`` used to be the one hand-written file, guarded by two
    tests, because the backend defined ``ErrorResponse`` without declaring it on
    any route -- so it never reached the OpenAPI document and no generator could
    see it. The backend now declares it on every guarded route
    (``tests/integration/test_error_envelope_is_in_the_openapi_document.py``),
    the file is deleted, and ``client.ts`` uses
    ``components["schemas"]["ErrorResponse"]`` like every other type.

    This test now asserts the *absence* of a class of file rather than guarding
    one instance of it, so a future hand-written type is caught when it appears
    instead of when someone remembers to add a guard.
    """
    api_dir = _WEB_ROOT / "src" / "lib" / "api"
    assert api_dir.is_dir(), "the API client directory moved"

    handwritten = [
        path.name
        for path in api_dir.glob("*.ts")
        if path.name not in {"types.ts", "client.ts", "index.ts"}
    ]
    assert not handwritten, (
        f"unexpected files in web/src/lib/api: {handwritten}. Every type here "
        "must come from the generated schema -- run `npm run generate:types` in "
        "web/ and import from ./types. A hand-written response type is a copy "
        "that drifts, which is what this client's design exists to prevent."
    )


# -- helpers -----------------------------------------------------------------


def _node_is_available() -> bool:
    """Whether a ``node`` executable is on PATH."""
    return shutil.which("node") is not None
