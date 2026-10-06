"""The error envelope must be in the OpenAPI document, not just at runtime.

``docs/api-contract.md`` §6 promises "one shape, everywhere": every failure,
from every endpoint, is ``{"error": {code, message, details}}``.
``src/opspilot/api/errors.py`` builds it through the Pydantic ``ErrorResponse``
model, and says so in a comment that claims this is "so the OpenAPI schema and
the runtime body cannot drift."

**The schema had no such entry.** ``ErrorResponse`` and ``ErrorBody`` were
defined in ``api/schemas.py`` but no route declared them, so FastAPI never
added them to ``components.schemas`` and any code generator building a client
from the document saw a contract with no error type at all.

The consequence was found the hard way, by a frontend agent asked to generate
its types from the live schema: it could not type a single failure response, so
it hand-wrote one -- reintroducing, in TypeScript, exactly the copy that
building from the schema was supposed to prevent. The comment claimed a
guarantee the code did not provide, and a green test suite could not see it,
because the suite had never asked the question.

A docstring asserting an invariant is not the invariant. This test asks the
schema.

Note ``web/src/lib/api/error-envelope.ts`` exists only because of this gap and
carries a test that fails *on purpose* the moment the schema gains the entry,
so the hand-written file is deleted rather than quietly kept in sync forever.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from opspilot.api.app import create_app
from opspilot.settings import get_settings


@pytest.fixture
def openapi_schema(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The application's OpenAPI document, built without a database.

    ``create_app`` with no stores injected falls back to the SQL adapters, but
    the OpenAPI document is assembled from the routers' declarations and never
    touches a store, so this needs no database. The operator token must be set,
    because the factory refuses to build without one -- deliberately. It is set
    per test rather than in the suite's shared fixture so that check stays
    meaningful everywhere else.
    """
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "openapi-document-test-token")
    monkeypatch.setenv("OPSPILOT_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    get_settings.cache_clear()
    app: FastAPI = create_app()
    return app.openapi()


def test_the_error_envelope_is_in_the_schema(openapi_schema: dict[str, object]) -> None:
    """``components.schemas`` carries both envelope models."""
    schemas = openapi_schema["components"]["schemas"]  # type: ignore[index]
    missing = [name for name in ("ErrorResponse", "ErrorBody") if name not in schemas]
    assert not missing, (
        f"{missing} are defined in api/schemas.py but absent from the OpenAPI "
        "document, so no generated client can see them. errors.py builds every "
        "runtime failure through ErrorResponse and claims the schema and the "
        "body cannot drift; they can, and did. Declare them on the routes -- "
        "a shared responses= mapping wired into each router -- so the document "
        "describes the failures it actually returns."
    )


def test_every_guarded_route_declares_the_error_envelope(
    openapi_schema: dict[str, object],
) -> None:
    """A generated client can type the failure of a route it calls.

    One 4xx declared somewhere is not enough: a client calling ``/api/runs``
    reads that operation's ``responses``, not the document's component list. If
    the envelope is only attached to one router, every other client still has to
    hand-write it.
    """
    paths = openapi_schema["paths"]
    missing: dict[str, list[str]] = {}
    for path, operations in paths.items():
        if not path.startswith("/api/"):
            continue
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = operation.get("responses", {})
            codes = {str(code) for code in responses}
            envelope_statuses = codes & {"400", "401", "404", "409", "422"}
            if not envelope_statuses:
                missing.setdefault(path, []).append(method.upper())
    assert not missing, (
        f"these operations declare no error response the client can type: "
        f"{missing}. docs/api-contract.md §6 says every endpoint returns the "
        "same envelope; the document has to say so for a generated client to "
        "believe it."
    )


def test_the_runtime_envelope_matches_the_declared_schema(
    openapi_schema: dict[str, object],
) -> None:
    """What a route returns is the shape the document declares.

    The claim in ``errors.py`` is that schema and body cannot drift. This
    compares them: the properties of the declared ``ErrorBody`` must be the
    properties actually emitted, so a field added to one and not the other
    fails here instead of in a client's parser.
    """
    from opspilot.api.errors import error_response

    schemas = openapi_schema["components"]["schemas"]
    declared = schemas["ErrorBody"]["properties"]
    emitted = error_response(code="x", message="y", details={"k": "v"})["error"]

    undeclared = sorted(set(emitted) - set(declared))
    assert not undeclared, (
        f"the runtime envelope emits {undeclared}, which the schema does not "
        "declare. errors.py builds the body from the ErrorResponse model, so "
        "this can only happen if the model and the document disagree."
    )
    required = schemas["ErrorBody"].get("required", [])
    missing = sorted(name for name in required if name not in emitted)
    assert not missing, (
        f"the schema declares {missing} required, but the runtime envelope does "
        f"not always emit them. Emitted: {sorted(emitted)}"
    )


def test_the_envelope_model_is_the_one_the_router_returns() -> None:
    """The declared schema is the model the runtime actually builds.

    Guards against the schema being fixed by *describing* an envelope while the
    handler keeps building a different one -- the two drifting apart in the
    other direction.
    """
    from opspilot.api.errors import error_response
    from opspilot.api.schemas import ErrorBody, ErrorResponse

    assert set(ErrorResponse.model_fields) == {"error"}
    assert set(ErrorBody.model_fields) == {"code", "message", "details"}
    body = error_response(code="not_found", message="m", details=None)
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}  # type: ignore[index]
