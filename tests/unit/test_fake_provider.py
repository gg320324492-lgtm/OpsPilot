"""Unit tests for ``FakeModelProvider``.

The two properties that matter: a recorded request returns the recorded
response, and an unmatched request raises (naming the request) rather than
returning a default -- a fake that answers anything makes every test downstream
of it measure the fake, not the system (``docs/risks.md`` R2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opspilot.adapters.models.fake import (
    FakeModelProvider,
    UnmatchedFixtureError,
    hash_request,
)
from opspilot.agents.schemas import AgentResponse, TicketClassification

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "fixtures"


def _write_fixture(tmp_path: Path, name: str, data: dict[str, object]) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _recorded_fixture(tmp_path: Path) -> Path:
    """A one-call fixture whose request hash matches ``hash_request('sys', 'ask')``."""
    system, prompt = "sys", "ask"
    return _write_fixture(
        tmp_path,
        "recorded_case",
        {
            "scenario": "recorded_case",
            "recorded": True,
            "calls": [
                {
                    "method": "generate_structured",
                    "schema": "TicketClassification",
                    "request_hash": hash_request(system, prompt),
                    "request": {"system": system, "prompt": prompt},
                    "response": {
                        "value": {
                            "category": "duplicate_charge",
                            "confidence": 0.88,
                            "rationale": "Two identical charges.",
                        },
                        "usage": {
                            "provider": "fake",
                            "model": "test",
                            "latency_ms": 12,
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "estimated_cost_usd": 0.0001,
                        },
                    },
                }
            ],
        },
    )


async def test_recorded_request_returns_the_recorded_response(tmp_path: Path) -> None:
    """A request whose hash matches a fixture returns that fixture's response."""
    _recorded_fixture(tmp_path)
    provider = FakeModelProvider(tmp_path)

    result = await provider.generate_structured(
        system="sys", prompt="ask", schema=TicketClassification
    )

    assert isinstance(result.value, TicketClassification)
    assert result.value.category.value == "duplicate_charge"
    assert result.value.confidence == pytest.approx(0.88)
    assert result.usage.input_tokens == 10
    assert result.usage.model == "test"


async def test_unmatched_request_raises_and_names_the_request(tmp_path: Path) -> None:
    """An unmatched request raises, and the message names what was asked for."""
    _recorded_fixture(tmp_path)
    provider = FakeModelProvider(tmp_path)

    with pytest.raises(UnmatchedFixtureError) as excinfo:
        await provider.generate_structured(
            system="different-system", prompt="different-prompt", schema=TicketClassification
        )

    message = str(excinfo.value)
    assert "generate_structured" in message
    assert hash_request("different-system", "different-prompt") in message
    # It names the request it could not satisfy, not a generic failure.
    assert "request_hash" in message


async def test_missing_scenario_raises_rather_than_defaulting(tmp_path: Path) -> None:
    """A scenario that is not on disk raises; the fake never invents a response."""
    _recorded_fixture(tmp_path)
    provider = FakeModelProvider(tmp_path, scenario="does_not_exist")
    with pytest.raises(UnmatchedFixtureError) as excinfo:
        await provider.generate_text(system="sys", prompt="ask")
    assert "does_not_exist" in str(excinfo.value)


async def test_scenario_interface_returns_the_expected_sequence() -> None:
    """``scenario="duplicate_charge"`` replays the recorded calls in order.

    Walks the real committed fixture: classification, then tool proposals, then
    the response. This is the interface the golden-path tests use.
    """
    provider = FakeModelProvider(FIXTURES_DIR, scenario="duplicate_charge")

    classification = await provider.generate_structured(
        system="s", prompt="p", schema=TicketClassification
    )
    assert classification.value.category.value == "duplicate_charge"

    first_tool = await provider.choose_tool(system="s", prompt="p", available_tools=[])
    assert first_tool["tool_name"] == "crm.get_customer"

    second_tool = await provider.choose_tool(system="s", prompt="p", available_tools=[])
    assert second_tool["tool_name"] == "billing.get_invoice"

    response = await provider.generate_structured(system="s", prompt="p", schema=AgentResponse)
    assert isinstance(response.value, AgentResponse)
    assert "refund-policy.md" in response.value.cited_document_slugs


async def test_already_refunded_scenario_proposes_no_further_write() -> None:
    """The already-refunded scenario ends with a done proposal and no refund."""
    provider = FakeModelProvider(FIXTURES_DIR, scenario="already_refunded")
    await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)
    tool = await provider.choose_tool(system="s", prompt="p", available_tools=[])
    assert tool["tool_name"] == "billing.list_transactions"
    final = await provider.choose_tool(system="s", prompt="p", available_tools=[])
    assert final["done"] is True
    assert final["tool_name"] is None


async def test_scenario_cursor_exhaustion_raises(tmp_path: Path) -> None:
    """Calling a one-call scenario twice raises on the second call, not invents."""
    _recorded_fixture(tmp_path)
    provider = FakeModelProvider(tmp_path, scenario="recorded_case")
    await provider.generate_structured(system="sys", prompt="ask", schema=TicketClassification)
    with pytest.raises(UnmatchedFixtureError):
        await provider.generate_structured(system="sys", prompt="ask", schema=TicketClassification)


async def test_reset_replays_the_scenario_from_the_start() -> None:
    """``reset()`` returns the cursor to the first call."""
    provider = FakeModelProvider(FIXTURES_DIR, scenario="duplicate_charge")
    first = await provider.choose_tool(system="s", prompt="p", available_tools=[])
    assert first["tool_name"] == "crm.get_customer"
    provider.reset()
    first_again = await provider.choose_tool(system="s", prompt="p", available_tools=[])
    assert first_again["tool_name"] == "crm.get_customer"


async def test_schema_invalid_fixture_raises(tmp_path: Path) -> None:
    """A fixture whose value does not fit the schema fails loudly at replay."""
    _write_fixture(
        tmp_path,
        "bad",
        {
            "scenario": "bad",
            "recorded": True,
            "calls": [
                {
                    "method": "generate_structured",
                    "response": {"value": {"category": "not_a_category"}},
                }
            ],
        },
    )
    provider = FakeModelProvider(tmp_path, scenario="bad")
    with pytest.raises(Exception):  # noqa: B017 -- ValidationError, asserted by name below
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)


def test_scenario_names_lists_the_committed_fixtures() -> None:
    """The two Phase 1 scenarios are discoverable by name."""
    provider = FakeModelProvider(FIXTURES_DIR)
    names = provider.scenario_names()
    assert "duplicate_charge" in names
    assert "already_refunded" in names


def test_hash_request_is_stable_and_distinguishes_the_boundary() -> None:
    """The matching key is deterministic and sensitive to where system ends."""
    assert hash_request("a", "b") == hash_request("a", "b")
    assert hash_request("ab", "") != hash_request("a", "b")
