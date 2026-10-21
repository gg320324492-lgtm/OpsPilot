"""The CI jobs the documents name must exist, and the jobs that exist must be named.

## Why this file exists

`docs/milestones.md` §M9 and the Phase 2 DoD's CI row both list the workflow's
job ids and say what they do. That is a claim about
`.github/workflows/ci.yml`, and it is the kind of claim that goes stale
quietly: a job is renamed, added or removed, and the ledger keeps naming the
old set.

It has already drifted once in the other direction. The `verify` job's
description listed three things it did — migrations, the vector differential,
the worker concurrency — and it now runs a fourth, the **migration-schema**
tests, which are the file whose own docstring tells the story of `0001`: a
migration that was green on SQLite for the whole of M0–M9 and had never
applied to a real PostgreSQL. A reader auditing the job from the ledger would
not have known the job covers it.

## What this file asserts

**Every job the workflows declare is named by a document.** A job nobody
mentions is a job whose failure nobody would read about, and it is the
direction that is easy to miss, because a job is added and the ledger is not
touched.

That one direction catches all three ways a job list goes stale:

* a job is **added** and nobody documents it -- the new job is not named;
* a job is **removed** and the documents still name it -- hmm, this one is not
  caught, because the check is "every job is named", not "every name is a
  job". A removed job leaves a name in the ledger that points at nothing;
* a job is **renamed** -- the documents still name the old id, and the new id
  is named by nobody, so the first case catches it.

The second shape -- a document naming a job that does not exist -- needs a
rule about *which backticked word is a job id*, because the documents name many
other things that way: a file, a setting, a GitHub action, a runner label, a
test function, an API variant. Two conditions together decide, and both are
needed:

1. **One edit away from a real job id.** A job that was renamed leaves the old
   name in the ledger, and the old name is one edit away from the new one.
   This is a strong signal and it is the one that catches a rename.
2. **On a line that already names a real job id.** This is what separates a
   job id from the other backticked words. The job lists in these documents
   are lists -- `` `lint`, `typecheck`, `test`, `verify` `` -- so a word
   sitting on a line that names a known job is in a job list, and a word on a
   line that names none is not.

Without the second condition the first one fires on ``text`` and ``test_``,
which are one edit from the four-letter job ``test``: ``text`` is an API
variant name in a compiler error message, and ``test_`` is a prefix the
documents use for "a test function". Both are real words in these documents
and neither is a job, and an exception list would be where a guard's false
negatives go to hide.

Both workflows are considered: `ci.yml` and the `workflow_dispatch`-only
`eval-live.yml`, whose `eval-live` job the ledger names too.
"""

from __future__ import annotations

import difflib
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = (
    REPO_ROOT / ".github" / "workflows" / "ci.yml",
    REPO_ROOT / ".github" / "workflows" / "eval-live.yml",
)

#: The documents that name the jobs.
_DOCUMENTS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "milestones.md",
    REPO_ROOT / "docs" / "phase2-plan.md",
    REPO_ROOT / "docs" / "architecture.md",
    REPO_ROOT / "docs" / "limitations.md",
    REPO_ROOT / "docs" / "progress.md",
)

#: A backticked token.
_TOKEN = re.compile(r"`([A-Za-z0-9_.\-/]+)`")


def _workflow_jobs() -> dict[str, str]:
    """Every job id the workflows declare, mapped to the file it is declared in."""
    jobs: dict[str, str] = {}
    for workflow in WORKFLOWS:
        if not workflow.exists():
            continue
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for name, body in (document.get("jobs") or {}).items():
            if isinstance(body, dict):
                jobs[name] = workflow.name
    return jobs


def _document_texts() -> dict[str, str]:
    """The documents that may name a job, and their text."""
    return {
        document.relative_to(REPO_ROOT).as_posix(): document.read_text(encoding="utf-8")
        for document in _DOCUMENTS
        if document.exists()
    }


def test_every_job_the_workflows_declare_is_named_by_a_document() -> None:
    """A job nobody mentions is a job whose failure nobody reads about."""
    jobs = _workflow_jobs()
    texts = _document_texts()
    all_text = "\n".join(texts.values())

    unnamed = sorted(job for job in jobs if f"`{job}`" not in all_text)
    assert not unnamed, (
        f"these CI jobs are not named by any document: {unnamed}. The workflow "
        "has grown a job the ledger does not mention, so nobody would read "
        "about it when it goes red."
    )


def test_every_job_id_the_documents_name_exists() -> None:
    """A job id the documents name must be a job the workflows declare.

    A backticked word is only a *candidate* when it is one edit away from a
    real job id **and** it sits on a line that already names a real job id.
    The first condition catches a rename; the second keeps the guard away from
    the file names, settings, test functions and API variants these documents
    also write in backticks.
    """
    jobs = _workflow_jobs()
    texts = _document_texts()

    suspects: dict[str, list[str]] = {}
    for name, text in texts.items():
        for line in text.splitlines():
            tokens = _TOKEN.findall(line)
            if not any(token in jobs for token in tokens):
                # A line that names no real job names no candidate either.
                continue
            for token in tokens:
                if token in jobs:
                    continue
                if any(_one_edit_apart(token, job) for job in jobs):
                    suspects.setdefault(token, []).append(name)

    assert not suspects, (
        "these look like CI job ids the documents name, but no such job "
        f"exists: {sorted(suspects)}. A renamed job is a claim about CI that "
        "cannot be checked."
    )
    print(f"\njobs in the workflows: {len(jobs)}; documents read: {len(texts)}")


def test_the_guard_finds_the_job_lists_it_is_meant_to_find() -> None:
    """The guard is not vacuously green.

    A guard that collects nothing checks nothing. This asserts that the
    documents really do carry job lists -- lines naming a real job id -- so a
    change in the rule above cannot quietly turn the second direction into a
    no-op that stays green.
    """
    jobs = _workflow_jobs()
    texts = _document_texts()

    lines_with_a_job = 0
    for text in texts.values():
        for line in text.splitlines():
            if any(token in jobs for token in _TOKEN.findall(line)):
                lines_with_a_job += 1

    assert lines_with_a_job >= 3, (
        f"only {lines_with_a_job} lines in the documents name a real job id, "
        "so the 'a renamed job is caught' rule has almost nothing to look at"
    )
    print(f"\nlines naming a real job id: {lines_with_a_job}")


def _one_edit_apart(left: str, right: str) -> bool:
    """Whether two names differ by at most one edit.

    ``difflib`` on the two strings, counting the edits a replacement and an
    insertion cost one each. A job id that is one edit from a real one is
    almost certainly a reference to a job that was renamed.
    """
    if left == right:
        return False
    matcher = difflib.SequenceMatcher(a=left, b=right)
    edits = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
    return edits <= 1
