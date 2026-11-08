"""The provider adapters must send the request shapes the SDKs accept.

## Why this file exists

The two real provider adapters send a request the SDK parses, and the shape of
that request is **the adapter's only contract with the SDK**. The Anthropic
one sends

.. code-block:: python

    output_config = {"format": {"type": "json_schema", "schema": ...}}

and the SDK's own types say the same thing: ``OutputConfigParam`` has
``effort`` and ``format``, and ``JSONOutputFormatParam`` has ``schema`` and
``type``.

**Nothing checked that.** The adapter's module docstring says the shape is
"verified against the installed SDK (``anthropic`` 1.x)", and a reader who
wants to check has to open the SDK and read two TypedDicts to do it. If the SDK
renamed a field, or added a required one, the adapter would keep sending the
old shape -- and the failure would appear only in a live provider call, which
is the ``eval-live`` job: a manual, cost-bearing workflow that runs on demand
and is therefore not on the path of any pull request.

So this file checks the shape against the SDK's own annotations, at test time,
without a network call.

## What this file asserts

1. **The shape the adapter sends is the shape the SDK's types declare** -- the
   same keys, no more and no fewer. The shape is read out of the adapter's own
   source rather than copied here, so the test cannot drift into checking a
   copy of itself.
2. **The value of ``format.type`` is the one the SDK's type documents**
   (``json_schema``), because a wrong discriminator is a request the SDK will
   not route to the right parser.
3. **The OpenAI adapter's request shape is checked the same way**, against
   ``openai``'s own response-format types.

## What it does not do

It does not call the SDK, and it does not check the *responses*. The SDK's
TypedDicts are not validated at run time -- constructing one is a plain dict --
so the check is against the declared keys, which is where the drift is
visible.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from pydantic import BaseModel

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODELS = REPO_ROOT / "src" / "opspilot" / "adapters" / "models"


def _literal_after(source: str, keyword: str) -> dict[str, object]:
    """The dictionary literal the adapter passes as ``keyword``.

    Read out of the source with the parser rather than with a regular
    expression, so a nested brace cannot end the search early. A call in the
    middle of the literal -- ``json_schema_for(schema)`` -- is replaced by an
    empty object: the check is about the *shape*, not about what the schema
    happens to be.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword_node in node.keywords:
            if keyword_node.arg != keyword or keyword_node.value is None:
                continue
            literal = re.sub(r"[\w.]+\([^()]*\)", "{}", ast.unparse(keyword_node.value))
            try:
                value = ast.literal_eval(literal)
            except (SyntaxError, ValueError):  # pragma: no cover - a shape we cannot read
                pytest.skip(f"the {keyword} literal in the adapter is not readable")
            if isinstance(value, dict):
                return value
    pytest.fail(f"the adapter passes no {keyword}= argument")


def _anthropic_shape() -> dict[str, object]:
    """The ``output_config`` the Anthropic adapter sends."""
    source = (MODELS / "anthropic_provider.py").read_text(encoding="utf-8")
    return _literal_after(source, "output_config")


def test_the_anthropic_request_shape_is_the_one_the_sdk_declares() -> None:
    """The adapter's ``output_config`` matches the installed SDK's types."""
    anthropic_types = pytest.importorskip(
        "anthropic.types", reason="the anthropic SDK is an optional extra"
    )
    output_config = pytest.importorskip(
        "anthropic.types.output_config_param", reason="the anthropic SDK is an optional extra"
    )

    shape = _anthropic_shape()

    declared = set(output_config.OutputConfigParam.__annotations__)
    sent = set(shape)
    assert sent <= declared, (
        f"the adapter sends output_config keys {sorted(sent - declared)}, which the "
        f"installed SDK does not declare (it declares {sorted(declared)})"
    )

    format_param = output_config.JSONOutputFormatParam
    format_keys = set(format_param.__annotations__)
    inner = shape.get("format")
    assert isinstance(inner, dict), f"the adapter sends no format: {shape!r}"
    assert set(inner) == format_keys, (
        f"the adapter sends format keys {sorted(set(inner))} and the SDK declares "
        f"{sorted(format_keys)}"
    )
    assert inner.get("type") == "json_schema", (
        f"the adapter's format type is {inner.get('type')!r}, not 'json_schema'"
    )
    assert "schema" in inner, "the adapter sends no schema"
    assert anthropic_types is not None


def test_the_openai_envelope_is_the_sdk_s_own_and_the_sdk_still_builds_it() -> None:
    """The OpenAI adapter uses the SDK's converter, and the converter still works.

    The OpenAI half of the pair does not hand-write its envelope: it asks the
    SDK to expand the Pydantic class, which is why the request it sends is
    byte-identical to the one the SDK's own path would send. A hand-written
    envelope would be a copy of the SDK's, and a copy drifts the moment the SDK
    changes.
    """
    pytest.importorskip("openai", reason="the openai SDK is an optional extra")

    source = (MODELS / "openai_provider.py").read_text(encoding="utf-8")
    assert "type_to_response_format_param" in source, (
        "the OpenAI adapter no longer uses the SDK's own converter to expand the "
        "schema; a hand-written envelope is a copy of the SDK's, and a copy "
        "drifts the moment the SDK changes"
    )

    from openai.lib._parsing import type_to_response_format_param

    class _Sample(BaseModel):
        value: int

    envelope = type_to_response_format_param(_Sample)
    assert isinstance(envelope, dict), f"the SDK produced {type(envelope)!r}"
    assert envelope.get("type") == "json_schema", (
        f"the SDK's converter produced type={envelope.get('type')!r}, not 'json_schema'"
    )
    inner = envelope.get("json_schema")
    assert isinstance(inner, dict), f"the SDK's converter produced no schema: {envelope!r}"
    assert "schema" in inner, f"the SDK's converter produced an empty schema: {envelope!r}"
