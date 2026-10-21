"""A document's claims about the eval corpus must match the corpus.

## Why this file exists

``docs/risks.md`` R3 -- the "approval becomes a rubber stamp" risk -- carried
this sentence in its mitigation:

    The eval's `safety` set includes cases where the correct human answer is
    Reject, and the demo scenario in the README shows a rejection path, not
    only an approval.

**The first clause was false, and false in the direction that flatters the
system.** The risk register was arguing that the eval demonstrates the
approval gate can be *refused*. The corpus does not contain a single refusal
case:

* every row of ``evals/datasets/safety.jsonl`` that carries an
  ``expected_terminal`` carries ``"completed"`` -- there is no rejected
  terminal anywhere in ``evals/datasets/``;
* the harness approves **every** parked run. ``runner.run_case`` says so in
  its own docstring: "The approval is unconditional, and that is a deliberate
  limitation of the harness, not of the system."

So the eval measures what the model *proposes* and what the gates *do*; it
cannot measure whether the human's judgement is right, because the human in
the harness always says yes. The second clause of the sentence was true -- the
README's scenario 2 does exercise a rejection -- but the doc leaned on a
false claim to make a true one sound better.

**Why no earlier guard caught it.** Every guard in this tree that touches the
eval corpus checks the *numbers* (the §4 table's denominators against the
datasets) or the *shape* (the CLI's printed table against the spec). Nothing
checked the prose, because checking prose is hard; and this particular claim
is invisible to a numeric guard, since it asserts the *presence* of a kind of
case rather than a count of it.

## What this file asserts

1. **The fact, computed from the datasets** -- the set of terminal outcomes
   the corpus actually holds, and that every one of them is a terminal status
   the domain defines. This is what a claim is checked against.
2. **The claim** -- for every document in the tree, the sentences that say
   the corpus *includes*/*contains*/*covers* cases of some outcome. A claim
   about an outcome the corpus does not hold is a false sentence, and the
   guard fails on it, naming the file and the clause.
3. **That the detector is neither vacuous nor trigger-happy** -- synthetic
   sentences are run through it, and the documents are counted, so a matcher
   that matches nothing cannot pass for a guard that is checking.

## What it does not do

It does not decide whether a sentence is *true* in general -- only whether
the corpus holds the outcome the sentence says it holds. A sentence about the
corpus that claims nothing about a terminal outcome is not this guard's
business, and is left alone.

It also does not police the historical record. ``docs/risks.md`` now says out
loud that the old sentence was wrong, and it quotes it. A sentence that
*denies* the claim ("this paragraph used to claim ...") is not a claim, and
the guard skips it -- but the synthetic test asserts that it really is skipped,
so the exemption is proved rather than assumed.
"""

from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASETS = REPO_ROOT / "evals" / "datasets"

#: The documents that talk about the eval corpus.
def _documents() -> list[pathlib.Path]:
    return [
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
        REPO_ROOT / "README.md",
        REPO_ROOT / "desktop" / "README.md",
        REPO_ROOT / "web" / "README.md",
    ]


# --------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------

#: A dataset row's ``expected_terminal``.
_TERMINAL_KEY = "expected_terminal"


def _dataset_rows() -> list[dict[str, object]]:
    """Every row of every dataset, in file order."""
    rows: list[dict[str, object]] = []
    for path in sorted(DATASETS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _corpus_terminals() -> set[str]:
    """The terminal outcomes the corpus actually holds."""
    return {
        str(row[_TERMINAL_KEY])
        for row in _dataset_rows()
        if row.get(_TERMINAL_KEY) is not None
    }


# --------------------------------------------------------------------------
# The claim detector
# --------------------------------------------------------------------------

#: A noun that names the corpus. Deliberately does not include "case" -- the
#: word is far too common to be evidence of anything.
_CORPUS_NOUN = re.compile(
    r"\b(eval|evals|evaluation|evaluations|safety set|benchmark|benchmarks|dataset|datasets|corpus)\b",
    re.IGNORECASE,
)

#: A verb that says the corpus *holds* something.
_INCLUDE = re.compile(
    r"\b(includes?|including|contains?|containing|covers?|covering|holds?|holding)\b",
    re.IGNORECASE,
)

#: A denial. One of these **before** the inclusion verb means the sentence is
#: not asserting the claim -- it is denying it or reporting it as history.
_DENIAL = re.compile(
    r"\b(used to|no longer|does not|do not|did not|doesn't|don't|cannot|can't|never)\b",
    re.IGNORECASE,
)

#: A clause: the unit the claim is looked for in. Commas, semicolons,
#: parentheses and em-dashes end one, because a sentence that says "the
#: recorded rejection reason is `gate_1_schema_validation`" is talking about
#: a *tool call* that a gate refused, not about a run that ended rejected --
#: and the two must not be confused.
_CLAUSE = re.compile(r"[^,;()—\n]+")

#: An expectation word: the sentence is about what a case is *supposed* to do.
_EXPECT = r"\b(expect\w*|expected|terminal|outcome|answer\w*|should|must)\b"

#: An outcome word: what a case could end as.
_OUTCOME = r"\b(reject\w*|refus\w*|declin\w*|deni\w*|complet\w*|failed|failure)\b"

#: An expectation and an outcome in one clause, in either order.
_CLAIM = re.compile(f"(?:{_EXPECT}[^.;]{{0,90}}?{_OUTCOME})|(?:{_OUTCOME}[^.;]{{0,90}}?{_EXPECT})", re.IGNORECASE)

#: How a prose outcome maps onto the corpus's ``expected_terminal`` values.
_CANONICAL: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("completed", re.compile(r"\bcomplet\w*\b", re.IGNORECASE)),
    ("failed", re.compile(r"\b(failed|failure)\b", re.IGNORECASE)),
    (
        "rejected",
        re.compile(r"\b(reject\w*|refus\w*|declin\w*|deni\w*)\b", re.IGNORECASE),
    ),
)


def _canonical_outcome(clause: str) -> str | None:
    """The outcome a clause names, or ``None`` if it names none."""
    for name, pattern in _CANONICAL:
        if pattern.search(clause):
            return name
    return None


def _claims_in(sentence: str) -> list[str]:
    """The outcomes a sentence claims the corpus holds.

    A sentence claims something when it names the corpus, carries an inclusion
    verb with no denial before it, and has an expectation and an outcome in one
    clause. Returns the canonical outcome names, in the order found.
    """
    if not _CORPUS_NOUN.search(sentence):
        return []
    include = _INCLUDE.search(sentence)
    if include is None:
        return []
    if _DENIAL.search(sentence[: include.start()]):
        # The sentence denies the claim, or reports that it used to make it.
        return []
    claimed: list[str] = []
    for clause in _CLAUSE.findall(sentence):
        if not _CLAIM.search(clause):
            continue
        outcome = _canonical_outcome(clause)
        if outcome is not None and outcome not in claimed:
            claimed.append(outcome)
    return claimed


def _sentences(text: str) -> list[str]:
    """The document's sentences."""
    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        out.extend(re.split(r"(?<=[.!?])\s+", paragraph))
    return out


def _document_claims() -> list[tuple[pathlib.Path, str, str]]:
    """Every (file, sentence, outcome) claim the documents make."""
    found: list[tuple[pathlib.Path, str, str]] = []
    for path in _documents():
        for sentence in _sentences(path.read_text(encoding="utf-8")):
            for outcome in _claims_in(sentence):
                found.append((path, sentence, outcome))
    return found


# --------------------------------------------------------------------------
# The tests
# --------------------------------------------------------------------------


def test_the_corpus_holds_only_terminal_statuses_the_domain_defines() -> None:
    """The fact the claims are checked against, computed from the datasets.

    Two things are pinned here. The corpus's expected terminals are a subset
    of the domain's *terminal* statuses -- a typo like ``"complete"`` would
    otherwise sail through every count-based guard and print as a run that
    finished when it never did. And the subset is exactly ``{"completed"}``:
    there is no refused, failed or abandoned case anywhere in the corpus.
    """
    from opspilot.domain.runs import ALLOWED_TRANSITIONS, RunStatus

    terminal = {status.value for status, successors in ALLOWED_TRANSITIONS.items() if not successors}
    assert terminal == {"completed", "failed"}, (
        f"the domain's terminal statuses are {sorted(terminal)}, not completed/failed; "
        "this guard's outcome vocabulary needs the new one"
    )

    held = _corpus_terminals()
    assert held, "no dataset row carries an expected_terminal; the guard would check nothing"

    unknown = sorted(held - terminal)
    assert not unknown, (
        f"the corpus expects terminal outcomes the domain does not define: {unknown}"
    )

    assert held == {"completed"}, (
        "the eval corpus holds terminal outcomes other than 'completed': "
        f"{sorted(held - {'completed'})}. If a case was added that expects a refused or "
        "failed run, this guard's pinned fact must be updated in the same commit -- and "
        "the harness's unconditional approval (src/opspilot/evals/runner.py::run_case) "
        "must be revisited with it, because such a case could never pass."
    )


def test_the_documents_claim_only_outcomes_the_corpus_holds() -> None:
    """No document claims the corpus holds a case it does not hold."""
    held = _corpus_terminals()
    wrong: list[str] = []
    for path, sentence, outcome in _document_claims():
        if outcome in held:
            continue
        where = path.relative_to(REPO_ROOT).as_posix()
        wrong.append(f"{where}: claims the corpus holds a '{outcome}' case -- {sentence.strip()[:160]}")
    assert not wrong, (
        "the documents claim outcomes the eval corpus does not hold "
        f"(the corpus holds {sorted(held)}):\n  "
        + "\n  ".join(wrong)
        + "\n\nEither the claim is wrong and the document should say what the corpus "
        "really contains, or the corpus is missing the case -- in which case the "
        "harness's unconditional approval has to change first, because a refused run "
        "cannot complete under it."
    )


def test_the_claim_detector_finds_a_claim_and_skips_a_denial() -> None:
    """The detector is neither vacuous nor trigger-happy.

    A regex guard that matches nothing is green for the wrong reason. These
    are the two sentences the detector has to get right: the false claim the
    guard exists to catch, and the corrected paragraph that quotes it in order
    to record that it was wrong.
    """
    claim = (
        "The eval's `safety` set includes cases where the correct human answer is Reject, "
        "and the demo scenario in the README shows a rejection path, not only an approval."
    )
    assert _claims_in(claim) == ["rejected"], (
        "the detector no longer finds the claim it exists to catch: "
        f"{_claims_in(claim)}"
    )

    denial = (
        "This paragraph used to claim that the eval's `safety` set includes cases where "
        "the correct human answer is Reject."
    )
    assert _claims_in(denial) == [], (
        "the detector treats a denial as a claim: " f"{_claims_in(denial)}"
    )


def test_the_detector_ignores_a_gate_rejection_that_is_not_a_run_outcome() -> None:
    """A refused *tool call* is not a refused *run*.

    The corpus's own documents talk a great deal about the 90 tool calls the
    gates refused, and that vocabulary must not be mistaken for a claim that
    the corpus holds a case whose run ended in refusal.
    """
    sentence = (
        "the committed baseline recomputes to twenty-two, the extra five being the four "
        "per-dataset splits of row 5 and the one gate its 90 rejections report"
    )
    assert _claims_in(sentence) == [], (
        f"a tool-call rejection was read as a run outcome: {_claims_in(sentence)}"
    )


def test_the_documents_were_actually_scanned() -> None:
    """The guard read something.

    Two ways a guard like this goes quietly blind: the document list stops
    matching the tree, or the corpus-noun pattern stops matching anything the
    documents say. Both are checked here, so a guard that would pass on an
    empty scan fails instead.
    """
    documents = _documents()
    assert len(documents) >= 10, f"only {len(documents)} documents are scanned"

    corpus_sentences = 0
    for path in documents:
        for sentence in _sentences(path.read_text(encoding="utf-8")):
            if _CORPUS_NOUN.search(sentence):
                corpus_sentences += 1
    assert corpus_sentences >= 50, (
        f"only {corpus_sentences} sentences in the tree name the corpus; the corpus-noun "
        "pattern has probably stopped matching the way the documents talk"
    )
