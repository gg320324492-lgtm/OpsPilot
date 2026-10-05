"""Eval runner: replay a dataset through the runtime.

Responsibility: load a JSONL dataset (``evals/datasets/*.jsonl``), run each case
through the workflow with the configured provider, and collect the raw outcomes
for scoring. Includes cases whose correct answer is "no document answers this",
so abstention is measured rather than assumed.

Layer: tooling. Runs on the fake provider by default so CI needs no API key; the
``live`` marker gates runs against a real provider, which are manual because
cost and non-determinism do not belong on every push.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel


class EvalCase(BaseModel):
    """One replayable eval case, loaded from a JSONL dataset line.

    Fields are the minimum the runner and metrics need; the datasets in
    ``evals/datasets/`` are the source of truth for the full shape.
    """

    id: str
    ticket_subject: str
    ticket_body: str
    expected_category: str | None = None
    expected_document_slugs: list[str] = []
    expects_abstention: bool = False


async def run_dataset(path: Path, *, provider_name: str) -> list[dict[str, object]]:
    """Run every case in a JSONL dataset and return the raw results. M0 stub."""
    raise NotImplementedError


async def run_case(case: EvalCase) -> dict[str, object]:
    """Run a single eval case through the workflow. M0 stub."""
    raise NotImplementedError


def load_dataset(path: Path) -> list[EvalCase]:
    """Load a JSONL dataset file. M0 stub."""
    raise NotImplementedError


def run_id_of(result: dict[str, object]) -> UUID | None:
    """Extract a run id from a result, if present. M0 stub."""
    raise NotImplementedError
