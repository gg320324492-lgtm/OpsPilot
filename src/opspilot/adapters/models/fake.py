"""``FakeModelProvider``: replays recorded fixtures.

Responsibility: a deterministic ``ModelProvider`` that returns recorded
responses from ``evals/datasets/fixtures/`` instead of calling a real model. It
is the default provider (``MODEL_PROVIDER=fake``) so a fresh clone and CI work
with no API key, and it is what the deterministic demo runs on.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

It is scriptable, which is how the security tests work: a test points it at a
fixture whose recorded response *complies with a prompt injection*, and asserts
the run still cannot execute the refund. The fake is how "the model is
encouraged to misbehave" is expressed as a test rather than a story.

**It replays, it does not invent.** Every response comes from a fixture captured
from a real provider interaction; an unmatched request raises
``UnmatchedFixtureError`` naming what was asked for, never a default. A fake that
quietly answers anything would make every test downstream of it measure the fake
rather than the system (``docs/risks.md`` R2). Fixtures are regenerable with
``record_fixtures`` below -- see ``record_fixture``.

Fixture format (one JSON file per scenario, under ``evals/datasets/fixtures/``)::

    {
        "scenario": "duplicate_charge",
        "recorded": false,  # false == hand-written placeholder, see below
        "calls": [
            {
                "method": "generate_structured",
                "schema": "TicketClassification",
                "request_hash": "sha256:...",
                "request": {"system": "...", "prompt": "..."},
                "response": {"value": {...}, "usage": {...}},
            }
        ],
    }

Matching, in order:

1. **Scenario name.** ``scenario="duplicate_charge"`` selects that file and
   returns its responses in order; the golden-path tests use this to say "play
   the duplicate-charge script". A call index advances per scenario.
2. **Request hash.** ``hash_request(system, prompt)`` -- a SHA-256 over both --
   selects the call whose ``request_hash`` matches. This is the replay path: the
   same prompt that produced the recorded response is what must come back.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from opspilot.ports.model_provider import ModelResponse, ModelUsage, TModel

# The scenario/name key written into every fixture.
_SCENARIO_KEY = "scenario"
_CALLS_KEY = "calls"


class UnmatchedFixtureError(LookupError):
    """No recorded response matched the request.

    Raised -- never swallowed into a default -- because a fake that answers
    anything makes every test downstream of it meaningless (``docs/risks.md``
    R2). The message names the method, the scenario (if one was requested), and
    the request hash, so an unmatched call says exactly what it could not find.
    """

    def __init__(self, *, method: str, scenario: str | None, request_hash: str) -> None:
        self.method = method
        self.scenario = scenario
        self.request_hash = request_hash
        where = f"scenario {scenario!r}" if scenario is not None else "the request hash"
        super().__init__(
            f"FakeModelProvider: no recorded {method} response matched {where} "
            f"(request_hash={request_hash}). Record a fixture or check the prompt."
        )


def hash_request(system: str, prompt: str) -> str:
    """The deterministic matching key for a request: SHA-256 over system+prompt.

    Both are hashed together with a separator that cannot appear in either by
    accident, so swapping the boundary between them changes the digest. The
    ``sha256:`` prefix makes it obvious in a fixture which algorithm produced it.
    """
    digest = hashlib.sha256()
    digest.update(system.encode("utf-8"))
    digest.update(b"\x00--system/prompt--\x00")
    digest.update(prompt.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


class FakeModelProvider:
    """A provider that replays recorded fixtures. No network, no SDK.

    Args:
        fixtures_dir: Directory of ``*.json`` fixtures. Defaults to
            ``evals/datasets/fixtures`` relative to the repository root.
        provider_name: Written into ``ModelUsage.provider`` so a trace says which
            provider answered. Defaults to ``"fake"``.
        scenario: When given, calls are answered from that scenario's ``calls``
            list in order, regardless of the request hash. This is the
            golden-path interface.
    """

    def __init__(
        self,
        fixtures_dir: Path | str | None = None,
        *,
        provider_name: str = "fake",
        scenario: str | None = None,
    ) -> None:
        self._fixtures_dir = (
            Path(fixtures_dir) if fixtures_dir is not None else _default_fixtures_dir()
        )
        self._provider_name = provider_name
        self._scenario = scenario
        # Per-method cursors: a scenario interleaves classify/choose_tool/respond
        # calls, and each method's responses are ordered relative to their own
        # kind, not to the others. A single shared cursor would let a classify
        # call consume a tool proposal's slot.
        self._scenario_cursors: dict[str, int] = {}
        self._fixtures: dict[str, dict[str, Any]] | None = None

    # -- fixture loading ---------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        """Load every fixture file once, keyed by scenario name."""
        if self._fixtures is None:
            fixtures: dict[str, dict[str, Any]] = {}
            if self._fixtures_dir.is_dir():
                for path in sorted(self._fixtures_dir.glob("*.json")):
                    data = json.loads(path.read_text(encoding="utf-8"))
                    name = data.get(_SCENARIO_KEY) or path.stem
                    fixtures[name] = data
            self._fixtures = fixtures
        return self._fixtures

    def scenario_names(self) -> list[str]:
        """The scenario names available in the fixtures directory."""
        return sorted(self._load())

    # -- matching ----------------------------------------------------------

    def _call_for(self, *, method: str, system: str, prompt: str) -> dict[str, Any]:
        """Return the fixture call record for this request, or raise.

        Scenario mode wins when a scenario was requested: the responses are
        returned in the order recorded, which is what "play the script" means.
        Otherwise the request hash selects the call.
        """
        fixtures = self._load()

        if self._scenario is not None:
            fixture = fixtures.get(self._scenario)
            if fixture is None:
                raise UnmatchedFixtureError(
                    method=method,
                    scenario=self._scenario,
                    request_hash=hash_request(system, prompt),
                )
            all_calls: list[dict[str, Any]] = fixture.get(_CALLS_KEY, [])
            calls = [c for c in all_calls if c.get("method") == method]
            index = self._scenario_cursors.get(method, 0)
            if index >= len(calls):
                raise UnmatchedFixtureError(
                    method=method,
                    scenario=self._scenario,
                    request_hash=hash_request(system, prompt),
                )
            self._scenario_cursors[method] = index + 1
            return calls[index]

        request_hash = hash_request(system, prompt)
        for fixture in fixtures.values():
            fixture_calls: list[dict[str, Any]] = fixture.get(_CALLS_KEY, [])
            for call in fixture_calls:
                if call.get("request_hash") == request_hash:
                    return call
        raise UnmatchedFixtureError(method=method, scenario=None, request_hash=request_hash)

    def _usage(self, call: dict[str, Any], *, default_model: str) -> ModelUsage:
        """Build the usage record, defaulting anything the fixture omitted."""
        raw = call.get("response", {}).get("usage", {})
        return ModelUsage(
            provider=str(raw.get("provider", self._provider_name)),
            model=str(raw.get("model", default_model)),
            latency_ms=int(raw.get("latency_ms", 0)),
            input_tokens=int(raw.get("input_tokens", 0)),
            output_tokens=int(raw.get("output_tokens", 0)),
            estimated_cost_usd=float(raw.get("estimated_cost_usd", 0.0)),
        )

    # -- ModelProvider protocol -------------------------------------------

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Replay a recorded structured response, validated into ``schema``.

        The recorded ``value`` is passed through ``schema.model_validate`` so a
        hand-written fixture that does not fit its schema fails loudly here
        rather than leaking an invalid object into the runtime.
        """
        _ = timeout_seconds
        call = self._call_for(method="generate_structured", system=system, prompt=prompt)
        value = schema.model_validate(call.get("response", {}).get("value", {}))
        return ModelResponse[TModel](
            value=value,
            usage=self._usage(call, default_model=schema.__name__),
        )

    async def choose_tool(
        self,
        *,
        system: str,
        prompt: str,
        available_tools: list[str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Replay a recorded tool proposal.

        The proposal is *not* validated against ``available_tools`` here: gate 2
        does that against the registry, and the fake must be able to replay a
        recorded proposal that names an unknown tool (a security test needs
        exactly that).
        """
        _ = (timeout_seconds, available_tools)
        call = self._call_for(method="choose_tool", system=system, prompt=prompt)
        raw: dict[str, Any] = call.get("response", {}).get("value", {})
        return dict(raw)

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Replay a recorded free-form reply."""
        _ = timeout_seconds
        call = self._call_for(method="generate_text", system=system, prompt=prompt)
        value = call.get("response", {}).get("value", "")
        return str(value)

    def reset(self) -> None:
        """Reset the scenario cursors so a scenario can be replayed from the start."""
        self._scenario_cursors.clear()


def _default_fixtures_dir() -> Path:
    """``evals/datasets/fixtures`` relative to this file's repository root.

    The package lives at ``<root>/src/opspilot/adapters/models/fake.py``; walking
    up four parents reaches ``<root>``. The directory may not exist when the
    package is installed as a wheel, in which case loading yields no fixtures and
    every call raises ``UnmatchedFixtureError`` -- the honest failure.
    """
    return Path(__file__).resolve().parents[4] / "evals" / "datasets" / "fixtures"


# ---------------------------------------------------------------------------
# Recording -- how the fixtures are regenerated rather than hand-written.
# ---------------------------------------------------------------------------


async def record_fixture(
    provider: Any,
    *,
    scenario: str,
    calls: list[dict[str, Any]],
    fixtures_dir: Path | str | None = None,
    recorded: bool = True,
) -> Path:
    """Record a real provider's responses into a fixture file.

    Each element of ``calls`` is a request descriptor::

        {"method": "generate_structured", "schema": "TicketClassification",
         "system": "...", "prompt": "..."}

    ``record_fixture`` calls the real ``provider`` with that request, captures
    the response, and writes ``{"scenario", "recorded", "calls": [...]}`` to
    ``<fixtures_dir>/<scenario>.json``. It is a plain coroutine rather than a CLI
    so it can be driven from a test or a one-off script; the equivalent manual
    command is::

        python -c "import asyncio; from opspilot.adapters.models.fake import \\
            record_fixture; from opspilot.adapters.models.anthropic_provider import \\
            AnthropicModelProvider; ...; asyncio.run(record_fixture(...))"

    This requires a real API key and network access, so it is not run in CI. The
    fixtures committed today are **hand-written placeholders** (``recorded:
    false``) until a real recording is made; see the README and ``docs/evals.md``.
    """
    target_dir = Path(fixtures_dir) if fixtures_dir is not None else _default_fixtures_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    recorded_calls: list[dict[str, Any]] = []
    for descriptor in calls:
        system = str(descriptor["system"])
        prompt = str(descriptor["prompt"])
        response = await _call_real(provider, descriptor, system=system, prompt=prompt)
        recorded_calls.append(
            {
                "method": descriptor["method"],
                "schema": descriptor.get("schema"),
                "request_hash": hash_request(system, prompt),
                "request": {"system": system, "prompt": prompt},
                "response": response,
            }
        )

    path = target_dir / f"{scenario}.json"
    path.write_text(
        json.dumps(
            {_SCENARIO_KEY: scenario, "recorded": recorded, _CALLS_KEY: recorded_calls},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


async def _call_real(
    provider: Any, descriptor: dict[str, Any], *, system: str, prompt: str
) -> dict[str, Any]:
    """Dispatch one request to a real provider and serialise its response."""
    method = descriptor["method"]
    if method == "generate_structured":
        schema = _resolve_schema(descriptor["schema"])
        result = await provider.generate_structured(system=system, prompt=prompt, schema=schema)
        return {
            "value": result.value.model_dump(mode="json"),
            "usage": result.usage.model_dump(mode="json"),
        }
    if method == "choose_tool":
        value = await provider.choose_tool(
            system=system,
            prompt=prompt,
            available_tools=list(descriptor.get("available_tools", [])),
        )
        return {"value": value, "usage": {}}
    if method == "generate_text":
        value = await provider.generate_text(system=system, prompt=prompt)
        return {"value": value, "usage": {}}
    raise _UnknownRecordMethod(method)


def _resolve_schema(name: str) -> type[Any]:
    """Resolve a schema name to the Pydantic class used to record it."""
    from opspilot.agents import schemas

    schema = getattr(schemas, name, None)
    if schema is None or not isinstance(schema, type):
        raise _UnknownSchema(name)
    return schema


class _UnknownRecordMethod(ValueError):
    """A ``record_fixture`` descriptor named a method the recorder cannot drive."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"unknown record method: {method!r}")


class _UnknownSchema(ValueError):
    """A ``record_fixture`` descriptor named a schema ``agents.schemas`` lacks."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"unknown schema: {name!r}")
