"""The classification dataset and the replay fixtures must agree about a ticket.

`evals/datasets/classification.jsonl` scores the CLASSIFYING step.
`evals/datasets/fixtures/duplicate_charge.json` replays the golden-path ticket
through the fake provider, and its classification step is what the whole golden
path is built on. They describe **the same ticket**.

They disagreed. `cls-001` is the README's golden-path ticket — "We were charged
twice for invoice INV-2026-384" — and the dataset expected `billing_dispute`
while the fixture emitted `duplicate_charge`. Neither label was invalid, so no
vocabulary guard could see it: **a vocabulary can be internally consistent and
still wrong about the world.** Three of the eight `billing_dispute` cases were
textually explicit duplicates ("charged twice", "double-charged again", "our
duplicate charge"), and not one of the twenty cases used `duplicate_charge` at
all until this was fixed.

This guard is the defence: it asserts the two artifacts give the same category to
the same ticket. It is deliberately narrow — the golden-path ticket only, because
that is the pair this project demonstrably got wrong, and a fuzzy text-match
across all twenty cases would be a guess dressed as a test.
"""

from __future__ import annotations

import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "evals" / "datasets" / "classification.jsonl"
FIXTURES = REPO_ROOT / "evals" / "datasets" / "fixtures"

# The ticket text both artifacts describe. The README's golden path and
# ``cls-001`` carry the same wording apart from the amount.
_GOLDEN_TICKET_MARKERS = ("charged twice", "INV-2026-384")
_markers_lowered = tuple(m.lower() for m in _GOLDEN_TICKET_MARKERS)


def _cases() -> list[dict[str, object]]:
    """Every classification case, read from the dataset."""
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fixture_classification(name: str) -> str | None:
    """The category a replay fixture's first structured call emits.

    Returns ``None`` rather than raising when the fixture carries no
    classification, so the caller's assertion carries the diagnostic -- an
    exception raised from a helper reports the helper, not what was compared.
    """
    fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    for call in fixture["calls"]:
        if call.get("method") != "generate_structured":
            continue
        value = call.get("response", {}).get("value", {})
        category = value.get("category")
        if isinstance(category, str):
            return category
    return None


def _is_golden_ticket(text: str) -> bool:
    """Whether ``text`` is the golden-path ticket, by two independent markers.

    Both sides are lower-cased. The first version compared the raw markers
    against the lowered text, so the mixed-case marker never matched and the
    guard reported "no such case" for the one ticket it exists to check -- a
    guard that fails loudly for the wrong reason is still a guard that lies.
    """
    lowered = text.lower()
    return sum(marker in lowered for marker in _markers_lowered) >= 2


def test_the_dataset_contains_the_golden_ticket() -> None:
    """The golden-path ticket is actually scored by the classification metric.

    Without this the test below would pass vacuously on a dataset that no longer
    covers the ticket the README demonstrates — which is how a guard can be
    green while checking nothing.
    """
    golden = [c for c in _cases() if _is_golden_ticket(str(c["input"]))]
    assert golden, (
        "no classification case matches the golden-path ticket; "
        f"expected one mentioning {list(_GOLDEN_TICKET_MARKERS)}"
    )


def test_the_golden_ticket_gets_the_same_category_from_both_artifacts() -> None:
    """The dataset and the fixture agree about the ticket they share.

    Read from the two files rather than asserted as a pair of literals: the
    failure this guards is precisely a pair of literals drifting apart.
    """
    golden = [c for c in _cases() if _is_golden_ticket(str(c["input"]))]
    assert len(golden) == 1, (
        f"expected exactly one golden-path case, found {len(golden)}; a second "
        "one would make 'which category does the fixture expect' ambiguous"
    )

    expected = str(golden[0]["expected_category"])
    from_fixture = _fixture_classification("duplicate_charge")

    assert from_fixture is not None, (
        "duplicate_charge.json emits no classification, so there is nothing for "
        "the dataset to agree with"
    )
    assert expected == from_fixture, (
        f"the dataset expects {expected!r} for the golden-path ticket but the "
        f"replay fixture emits {from_fixture!r}. The classification metric and "
        "the golden path describe the same ticket and must score it the same "
        "way -- otherwise the number the README quotes and the run the reader "
        "can watch are answers to different questions."
    )


def test_the_duplicate_charge_label_is_actually_exercised() -> None:
    """``duplicate_charge`` is used by the dataset, not merely defined.

    The enum member and the fixture both existed while *no case* used the label,
    so the metric never tested the one category the golden path runs on. A
    vocabulary that defines a value nothing exercises is decoration.
    """
    labels = {str(case["expected_category"]) for case in _cases()}
    assert "duplicate_charge" in labels, (
        "no classification case expects duplicate_charge, so the golden path's "
        "own category is never scored; labels present: " + ", ".join(sorted(labels))
    )
