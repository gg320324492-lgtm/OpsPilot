"""The fixtures must say what they are, and the provider must say the same.

## Why this file exists

``src/opspilot/adapters/models/fake.py`` is the provider every test in the
repository runs on. Its module docstring said:

    **It replays, it does not invent.** Every response comes from a fixture
    captured from a real provider interaction.

That was false, and the fixtures said so themselves. Both committed fixtures
carry ``"recorded": false`` and a note reading "HAND-WRITTEN PLACEHOLDER, not a
recording", and **neither carries a ``request_hash``** -- so the request-hash
replay path the same docstring describes has nothing committed to match
against. The scenario path is what serves them.

``docs/risks.md`` R2 records the same fact, and the guard
``test_the_live_fixture_recording_is_marked_as_a_recording.py`` holds the
``recorded`` flag's meaning. This file holds the *agreement* between the flag,
the fixtures and the docstring, because the flag is only worth anything if
everybody reads it the same way.

## What this file asserts

1. **Every committed fixture declares ``recorded``**, and says why in a note.
2. **A hand-written fixture carries no request hash**, and a recording carries
   one on every call -- the flag has to mean the thing it says.
3. **A recording's calls carry the hash the provider would compute**, so the
   replay path cannot silently match nothing.
4. **The provider's docstring says what the fixtures are.** If every committed
   fixture is a hand-written placeholder, the docstring may not claim the
   responses came from a real provider.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, cast

from opspilot.adapters.models.fake import hash_request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "evals" / "datasets" / "fixtures"
FAKE_SOURCE = REPO_ROOT / "src" / "opspilot" / "adapters" / "models" / "fake.py"


def _fixtures() -> list[dict[str, object]]:
    """The committed fixtures, parsed."""
    out: list[dict[str, object]] = []
    for path in sorted(FIXTURES.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        document["_path"] = path.name
        out.append(document)
    return out


def test_every_fixture_declares_whether_it_was_recorded() -> None:
    """The ``recorded`` flag is present, and a hand-written one says so."""
    fixtures = _fixtures()
    assert fixtures, "no fixture is committed; the fake provider has nothing to replay"

    for fixture in fixtures:
        name = fixture["_path"]
        assert "recorded" in fixture, f"{name} does not say whether it was recorded"
        assert isinstance(fixture["recorded"], bool), (
            f"{name}'s 'recorded' is {fixture['recorded']!r}, which is neither "
            "true nor false; the flag has to mean one of them"
        )
        note = str(fixture.get("note") or "")
        assert note, f"{name} has no note, so the flag has nothing to qualify it"
        if fixture["recorded"] is False:
            assert "placeholder" in note.lower() or "hand-written" in note.lower(), (
                f"{name} says it was not recorded, but its note does not say what "
                f"it is instead: {note!r}"
            )


def test_the_recorded_flag_agrees_with_the_request_hashes() -> None:
    """A recording carries hashes; a hand-written one does not."""
    for fixture in _fixtures():
        name = fixture["_path"]
        calls = cast("list[dict[str, Any]]", fixture.get("calls") or [])
        assert calls, f"{name} has no calls"

        hashes = [call.get("request_hash") for call in calls]
        if fixture["recorded"] is True:
            missing = [index for index, value in enumerate(hashes) if not isinstance(value, str)]
            assert not missing, (
                f"{name} claims to be a recording, but calls {missing} carry no "
                "request_hash. The replay path matches on the hash, so a "
                "recording without one can never be served by it."
            )
        else:
            present = [index for index, value in enumerate(hashes) if isinstance(value, str)]
            assert not present, (
                f"{name} says it is hand-written, but calls {present} carry a "
                "request_hash. One of the two is wrong, and a hand-written "
                "fixture with a hash would be served by the replay path rather "
                "than the scenario path."
            )


def test_a_recording_carries_the_hash_the_provider_would_compute() -> None:
    """A recorded hash is the hash of that call's own request."""
    for fixture in _fixtures():
        if fixture["recorded"] is not True:
            continue
        name = fixture["_path"]
        for index, call in enumerate(cast("list[dict[str, Any]]", fixture.get("calls") or [])):
            request = call.get("request") or {}
            expected = hash_request(str(request.get("system", "")), str(request.get("prompt", "")))
            assert call.get("request_hash") == expected, (
                f"{name} call[{index}] carries a request_hash that is not the hash "
                "of its own request, so it would never be matched"
            )


def test_the_provider_says_what_the_committed_fixtures_are() -> None:
    """The docstring may not claim a provenance the fixtures do not have."""
    fixtures = _fixtures()
    hand_written = [f for f in fixtures if f.get("recorded") is not True]
    docstring = FAKE_SOURCE.read_text(encoding="utf-8").split('"""', 2)[1]

    if hand_written and len(hand_written) == len(fixtures):
        assert "hand-written" in docstring or "placeholder" in docstring, (
            "every committed fixture says it is hand-written, and the provider's "
            "docstring does not say so"
        )
        assert not re.search(
            r"every\s+response\s+comes\s+from\s+a\s+fixture\s+captured\s+from\s+a\s+real\s+provider",
            docstring,
            re.IGNORECASE,
        ), (
            "the provider's docstring claims every response comes from a fixture "
            "captured from a real provider, and every committed fixture says it "
            "is hand-written"
        )
