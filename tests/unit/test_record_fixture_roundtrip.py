"""``record_fixture`` produces a file ``FakeModelProvider`` can replay.

## Why this file exists

``docs/milestones.md`` §M4 has one criterion left unticked: the fixtures must be
**recorded** from a real provider, not hand-written. Both committed fixtures
carry ``"recorded": false`` and a note reading "HAND-WRITTEN PLACEHOLDER, not a
recording. Regenerate with record_fixture() against a real provider".

So the honest position is that the *responses* are hand-written. What was never
established is whether the *mechanism* works: ``record_fixture`` has no test at
all, and a recorder that cannot be replayed would make the remaining work --
one run against a real provider -- fail after it had already spent the key.

These tests close the half that needs no API key and no network. They drive the
recorder with a stub provider and then replay what it wrote, which proves the
round trip: what the recorder writes, the replayer reads back, byte for byte in
meaning if not in layout. The other half -- that a *real* provider's responses
are what lands in the file -- is exactly the part that needs the key, and is
still outstanding.

## What is asserted

1. The recorder writes the five fields the replayer reads, including a
   ``request_hash`` the committed placeholders do not carry.
2. **What it writes is replayed back with the same value** -- the property the
   criterion is about, and the one a shape assertion alone would not prove.
3. Matching is by hash, not by position: a different prompt is refused. Without
   this, a fixture could satisfy a request it was never recorded for, which is
   the failure ``docs/risks.md`` R2 is written against.
4. ``recorded: true`` is what the recorder writes by default -- the flag the two
   committed fixtures carry as ``false`` is the thing the missing run flips.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opspilot.adapters.models.fake import (
    FakeModelProvider,
    UnmatchedFixtureError,
    hash_request,
    record_fixture,
)
from opspilot.agents.schemas import AgentResponse, TicketClassification

#: The request the recorder is driven with, and the replay is matched against.
SYSTEM = "You are classifying a support ticket."
PROMPT = "Subject: We were charged twice for invoice INV-2026-384"

#: A provider-shaped object that answers once, deterministically.
#:
#: It is not ``FakeModelProvider``: the recorder calls the *real* provider
#: protocol, so the stub has to implement ``generate_structured`` the way a
#: provider does -- return an object with ``.value`` and ``.usage`` -- rather
#: than replay a script.


class _StubUsage:
    """The usage half of a provider result: a model that dumps to JSON."""

    def model_dump(self, *, mode: str) -> dict[str, object]:  # noqa: ARG002 -- protocol shape
        return {
            "provider": "stub",
            "model": "stub-model",
            "latency_ms": 1,
            "input_tokens": 100,
            "output_tokens": 20,
            "estimated_cost_usd": 0.0,
        }


class _StubResult:
    def __init__(self, value: object, usage: _StubUsage) -> None:
        self.value = value
        self.usage = usage


class _StubProvider:
    """Answers ``generate_structured`` with a fixed, schema-valid value."""

    async def generate_structured(
        self,
        *,
        system: str,  # noqa: ARG002 -- fixed by the provider protocol, unused here
        prompt: str,  # noqa: ARG002 -- ditto
        schema: type[TicketClassification],
    ) -> _StubResult:
        value = schema.model_validate(
            {
                "category": "duplicate_charge",
                "confidence": 0.93,
                "rationale": "The same invoice appears twice in the transactions.",
            }
        )
        return _StubResult(value, _StubUsage())


class _FailsOnSecondCall(_StubProvider):
    """Succeeds once, then raises -- the recording that dies partway through.

    This is the shape a real recording takes when the endpoint rate-limits
    mid-run, which is what happened on 2026-10-15: the key was valid and the
    endpoint reachable, but ``HTTP 429`` (MiniMax code 2056, Token Plan
    exhausted) came back on every request. A multi-call recording against such
    an endpoint gets some answers and then a failure, so what happens to the
    answers already collected is the question this stub exists to ask.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TicketClassification],
    ) -> _StubResult:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("HTTP 429: Token Plan quota exhausted")
        return await super().generate_structured(system=system, prompt=prompt, schema=schema)


@pytest.fixture
async def recorded_file(tmp_path: Path) -> Path:
    """A fixture written by ``record_fixture`` into a throwaway directory.

    An async fixture, because ``record_fixture`` is a coroutine and
    ``asyncio.run`` cannot be nested inside a running loop -- pytest-asyncio
    already provides one, and calling ``asyncio.run`` here would fail with
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``.
    """
    path = await record_fixture(
        _StubProvider(),
        scenario="recorded_probe",
        calls=[
            {
                "method": "generate_structured",
                "schema": "TicketClassification",
                "system": SYSTEM,
                "prompt": PROMPT,
            }
        ],
        fixtures_dir=tmp_path,
    )
    assert path.is_file()
    return path


def test_the_recorder_writes_every_field_the_replayer_reads(
    recorded_file: Path,
) -> None:
    """The written file carries the five keys, and ``recorded`` is true.

    The two committed placeholders carry ``recorded: false`` and no
    ``request_hash`` at all, so this is the difference the outstanding run makes
    -- not a detail of the format.
    """
    data = json.loads(recorded_file.read_text(encoding="utf-8"))

    assert data["recorded"] is True, (
        "the recorder wrote recorded: false; the flag that marks a real "
        "recording is not what the tool produces"
    )
    assert data["scenario"] == "recorded_probe"

    call = data["calls"][0]
    # ``request_hash`` and ``response`` are what the replayer reads
    # (``fake.py`` matches on the hash alone); ``request`` is provenance for a
    # human opening the file, and ``method``/``schema`` say which call it was.
    for field in ("method", "schema", "request_hash", "response"):
        assert field in call, f"the recorder did not write {field!r}; the replayer reads it"
    assert "request" in call, "the recorder did not write the human-readable request"

    assert call["request_hash"] == hash_request(SYSTEM, PROMPT), (
        "the written hash does not match hash_request(system, prompt); the "
        "replayer would never match this file against the request it recorded"
    )
    assert call["request"] == {"system": SYSTEM, "prompt": PROMPT}, (
        "the provenance copy of the request does not match what was recorded; "
        "someone reading the fixture would be told the wrong question"
    )


async def test_what_the_recorder_wrote_is_replayed_back_with_the_same_value(
    recorded_file: Path,
) -> None:
    """The round trip: record, then replay, and get the recorded value.

    This is the property the M4 criterion is about. The responses being
    hand-written is the outstanding half; the *machinery* carrying a real
    response from a provider into a file and back out to a caller is this half,
    and it needs no key.
    """
    provider = FakeModelProvider(recorded_file.parent)

    result = await provider.generate_structured(
        system=SYSTEM, prompt=PROMPT, schema=TicketClassification
    )

    assert result.value.category.value == "duplicate_charge"
    assert result.value.confidence == pytest.approx(0.93)
    assert result.value.rationale == "The same invoice appears twice in the transactions."
    assert result.usage.input_tokens == 100


async def test_a_request_the_recorder_did_not_see_is_refused(
    recorded_file: Path,
) -> None:
    """Matching is by hash, so a neighbouring prompt gets no answer.

    If the replayer matched loosely -- by position, or by method alone -- a
    fixture could satisfy a request it was never recorded for, and every test
    downstream would measure the fixture rather than the system. ``R2`` in
    ``docs/risks.md`` is written against exactly that.
    """
    provider = FakeModelProvider(recorded_file.parent)

    with pytest.raises(UnmatchedFixtureError) as excinfo:
        await provider.generate_structured(
            system=SYSTEM, prompt="a different prompt entirely", schema=TicketClassification
        )

    assert hash_request(SYSTEM, "a different prompt entirely") in str(excinfo.value)


async def test_the_recorder_rejects_a_method_it_cannot_drive(tmp_path: Path) -> None:
    """An unknown method is a loud failure, not a silently empty recording.

    ``_call_real`` dispatches on ``method``; a descriptor naming something else
    must raise rather than write a fixture missing a response, which would look
    like a recorded answer of nothing.
    """
    with pytest.raises(ValueError, match="unknown record method"):
        await record_fixture(
            _StubProvider(),
            scenario="bad_method",
            calls=[{"method": "no_such_method", "system": "s", "prompt": "p"}],
            fixtures_dir=tmp_path,
        )

    assert not (tmp_path / "bad_method.json").exists(), (
        "a failed recording left a file behind; a later replay would read a "
        "fixture with no response in it"
    )


def test_the_committed_fixtures_are_still_the_hand_written_ones() -> None:
    """The two committed fixtures still say ``recorded: false``.

    This is the assertion that keeps this file honest. It fails the day someone
    flips those flags without actually running the recorder against a real
    provider -- which is the one thing that would make the M4 criterion true,
    and the one thing that has not happened.
    """
    fixtures = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "fixtures"
    for name in ("duplicate_charge", "already_refunded"):
        data = json.loads((fixtures / f"{name}.json").read_text(encoding="utf-8"))
        assert data["recorded"] is False, (
            f"{name}.json now claims to be a recording. If record_fixture was "
            "run against a real provider to produce it, tick the §M4 criterion "
            "and update this test; if it was not, the flag is a false claim"
        )


async def test_a_recording_that_dies_partway_writes_no_file(tmp_path: Path) -> None:
    """A provider failure mid-recording raises and leaves nothing behind.

    This is the shape the outstanding real recording would take if it were run
    against an endpoint that rate-limits: some calls succeed, then one raises.
    ``record_fixture`` collects every response *before* it opens the file
    (``fake.py:307-328``), so a failure on call two discards call one's answer
    and writes nothing -- which is the correct behaviour, and is why this is
    safe to run against a metered key. The alternative, a partially written
    fixture, would look like a recording of an endpoint that answered only some
    of the questions, and would replay as one.

    Pinned because it is the failure path a real run is most likely to hit, and
    nothing else in the suite exercises it: the other tests either succeed on
    every call or fail on the first.
    """
    provider = _FailsOnSecondCall()

    with pytest.raises(RuntimeError, match="429"):
        await record_fixture(
            provider,
            scenario="dies_partway",
            calls=[
                {
                    "method": "generate_structured",
                    "schema": "TicketClassification",
                    "system": SYSTEM,
                    "prompt": PROMPT,
                },
                {
                    "method": "generate_structured",
                    "schema": "TicketClassification",
                    "system": SYSTEM,
                    "prompt": "a second question the endpoint never answers",
                },
            ],
            fixtures_dir=tmp_path,
        )

    assert provider.calls == 2, (
        "the recorder stopped before the failing call; this test only proves "
        "something about partial writes if the second call was actually made"
    )
    assert not (tmp_path / "dies_partway.json").exists(), (
        "a recording that failed on its second call left a file behind; the "
        "first call's answer was persisted as if it were the whole recording"
    )
    assert list(tmp_path.iterdir()) == [], (  # noqa: ASYNC240 -- a tmp_path assertion, not I/O in a hot loop
        f"the failed recording left something in the directory: "
        f"{[p.name for p in tmp_path.iterdir()]}"  # noqa: ASYNC240 -- ditto
    )


def test_a_recorded_agent_response_round_trips_through_its_schema() -> None:
    """The recorder's output fits the schema it names, not just any dict.

    ``response.value`` is written with ``model_dump(mode="json")``, so this is
    the check that a real provider's answer -- which Pydantic validates on the
    way out -- survives the file. A response that validated but did not dump
    cleanly would fail here rather than at replay time in an eval run.
    """
    dumped = AgentResponse(
        body="We have refunded the duplicate charge.",
        cited_document_slugs=["refund-policy.md"],
    ).model_dump(mode="json")

    # The dump is what record_fixture writes and what the replayer validates
    # against the same schema, so this is the round trip without a provider.
    restored = AgentResponse.model_validate(dumped)
    assert restored.body == dumped["body"]
    assert restored.cited_document_slugs == ["refund-policy.md"]
    assert restored.escalated is False
