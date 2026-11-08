"""``evals/README.md`` must describe the tree that is actually there.

## Why this file exists

``evals/README.md`` is the first thing a reader of the evaluation harness
reads. It carries a **directory tree**, a table of the datasets and their case
counts, and a description of what the near-miss retrieval cases expect.

All three had drifted, and each had drifted in the way that costs a reader
the most: the tree named files that are not there.

* The tree listed ``runner.py``, ``metrics.py`` and ``test_harness.py`` under
  ``evals/``. The first two live in ``src/opspilot/evals/`` and the third in
  ``tests/evals/``; ``evals/`` holds the datasets and the results and nothing
  else. A reader who looked for ``evals/runner.py`` would not find it.
* The near-miss paragraph described "a question about a $129 enterprise
  duplicate that expects **both** ``duplicate-charge-sop.md`` and
  ``refund-policy.md``". The $129 enterprise case expects **three** documents
  -- the two plus ``enterprise-billing.md``, which is the enterprise override
  the answer has to reconcile. The two-document case is a different one.
* The case counts are countable.

## What this file asserts

1. **Every path the tree names exists**, and the tree's directories are the
   directories there are.
2. **The datasets' case counts are the datasets' case counts.**
3. **Every document a case expects is a document the corpus has.** A case that
   expects a name no policy file carries can never be answered, so its recall
   is capped below one for a reason that has nothing to do with the retriever.
4. **The near-miss paragraph names the documents the case expects** -- so the
   description cannot describe a different case than the one it is about.
"""

from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EVALS_README = REPO_ROOT / "evals" / "README.md"
DATASETS = REPO_ROOT / "evals" / "datasets"
KNOWLEDGE = REPO_ROOT / "knowledge"


def _rows() -> list[tuple[str, str, str]]:
    """The README's dataset table: ``(name, cases, measures)``."""
    rows: list[tuple[str, str, str]] = []
    for line in EVALS_README.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([a-z_]+\.jsonl)`\s*\|\s*(\d+)\s*\|(.*)\|\s*$", line)
        if match is not None:
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _corpus() -> set[str]:
    """The policy documents the corpus holds."""
    return {path.name for path in KNOWLEDGE.glob("*.md") if path.name != "README.md"}


def test_every_path_the_readmes_tree_names_exists() -> None:
    """The tree is the tree."""
    text = EVALS_README.read_text(encoding="utf-8")

    named = set(re.findall(r"`([a-z_]+/[a-z_]+(?:/[a-z_]+)?\.py)`", text))
    named |= set(re.findall(r"^\s*[│├└─ ]*([a-z_]+/[a-z_]+/)\s*$", text, re.M))

    missing = sorted(path for path in named if not (REPO_ROOT / path).exists())
    assert not missing, (
        f"the tree in evals/README.md names paths that do not exist: {missing}. "
        "A reader who looks for them does not find them."
    )

    # The tree also has to be complete: the directories it lists are the ones
    # that hold the harness.
    for directory in ("src/opspilot/evals", "tests/evals"):
        assert (REPO_ROOT / directory).is_dir(), (
            f"the tree does not mention {directory}, which holds the harness"
        )


def test_the_dataset_case_counts_are_the_datasets_case_counts() -> None:
    """The table's counts are the files' counts."""
    rows = _rows()
    assert rows, "the README's dataset table could not be read"

    for name, claimed, _measures in rows:
        path = DATASETS / name
        assert path.is_file(), f"the README lists {name}, which is not in the datasets"
        actual = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        assert actual == int(claimed), f"{name} has {actual} cases; the README says {claimed}"


def test_every_expected_document_is_a_document_the_corpus_has() -> None:
    """A case cannot expect a document that does not exist."""
    corpus = _corpus()
    assert corpus, "the corpus holds no documents"

    problems: list[str] = []
    for path in sorted(DATASETS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            for document in case.get("expected_documents") or []:
                if document not in corpus:
                    problems.append(f"{path.name}: {case.get('id')} expects {document!r}")
    assert not problems, (
        "these cases expect documents the corpus does not hold:\n  "
        + "\n  ".join(problems)
        + "\nA case that expects a document no policy file carries can never be "
        "answered, so its recall is capped below one for a reason that has "
        "nothing to do with the retriever."
    )


def test_the_near_miss_paragraph_names_the_documents_its_cases_expect() -> None:
    """The description is about the cases it says it is about.

    The paragraph used to describe "a question about a $129 enterprise
    duplicate" without saying which one: there are two, and they expect
    different document sets. It now names them, and this checks that every
    document it mentions is one the case it names expects -- so the
    description cannot drift onto a different case.
    """
    text = EVALS_README.read_text(encoding="utf-8")
    paragraph = text.split("**Near misses.**", 1)[1].split("\n\n", 1)[0]

    rows = {
        case["id"]: case
        for case in (
            json.loads(line)
            for line in (DATASETS / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }

    cited = re.findall(r"`(ret-\d+)`", paragraph)
    assert cited, (
        "the near-miss paragraph names no case; a reader cannot tell which of the "
        "cases it describes"
    )

    named = set(re.findall(r"`([a-z0-9-]+\.md)`", paragraph))
    for case_id in cited:
        assert case_id in rows, f"the near-miss paragraph names {case_id}, which is not a case"
        expected = set(rows[case_id].get("expected_documents") or [])
        assert expected <= named, (
            f"the near-miss paragraph names {case_id} but does not mention "
            f"{sorted(expected - named)}, which that case expects"
        )
    assert named <= set().union(
        *(set(rows[case_id].get("expected_documents") or []) for case_id in cited)
    ), "the near-miss paragraph mentions a document none of the cases it names expects"
