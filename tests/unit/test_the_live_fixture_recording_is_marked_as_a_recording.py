"""A live recording must be marked as one.

``scripts/record_live_fixture.py`` exists for exactly one reason: the two
committed fixtures under ``evals/datasets/fixtures/`` carry
``"recorded": false``, because they are hand-written. The script's whole point
is to produce the opposite -- calls that crossed a real provider.

It used to write a bare JSON **list** of calls. A list carries no
``recorded`` flag, so neither a reader nor a promotion script could tell a
live recording from a hand-written one, and the file was not the fixture shape
either: the committed fixtures are
``{"scenario", "recorded", "note", "calls"}``.

This pins the shape, so the flag cannot quietly go missing again.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib
from types import ModuleType

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "record_live_fixture.py"
FIXTURES = REPO / "evals" / "datasets" / "fixtures"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    """The recording script, loaded from its path.

    Loaded rather than imported so the test does not depend on ``scripts/``
    being an importable package; it is a directory of programs, not a library.
    """
    spec = importlib.util.spec_from_file_location("record_live_fixture_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_recording_document_is_marked_as_recorded(script: ModuleType) -> None:
    """The document carries ``recorded: true`` and the calls it holds."""
    calls = [{"method": "generate_structured", "schema": "X", "request": {}, "response": {}}]
    document = script.fixture_document(calls)

    assert document["recorded"] is True
    assert document["calls"] == calls
    assert document["scenario"]
    assert document["note"]


def test_the_recording_scenario_cannot_overwrite_a_committed_fixture(script: ModuleType) -> None:
    """The live recording is filed under a name of its own.

    The committed fixtures are named after the fake provider's scenarios. A
    recording filed under one of those names would be one careless ``mv``
    away from replacing a committed fixture with a different set of calls.
    """
    committed = {
        json.loads(path.read_text(encoding="utf-8"))["scenario"] for path in FIXTURES.glob("*.json")
    }
    assert committed, "no committed fixture was found, so this test proves nothing"
    assert script.SCENARIO not in committed


def test_the_committed_fixtures_are_still_marked_as_not_recorded() -> None:
    """The flag only means something because the committed fixtures are false."""
    flags = {
        path.name: json.loads(path.read_text(encoding="utf-8"))["recorded"]
        for path in FIXTURES.glob("*.json")
    }
    assert flags, "no committed fixture was found, so this test proves nothing"
    assert all(flag is False for flag in flags.values()), flags


def test_the_script_writes_the_document_rather_than_a_bare_list(script: ModuleType) -> None:
    """``main()`` must write what :func:`fixture_document` builds.

    A helper that builds the right shape and a ``main()`` that writes a bare
    list is the drift this file exists for, in its second form.
    """
    source = inspect.getsource(script.main)
    assert "fixture_document(" in source, (
        "main() does not write the fixture-shaped document; it writes something else:\n" + source
    )
