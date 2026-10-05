"""Deterministic eval metrics.

Responsibility: score a set of eval results. Phase 1 metrics are all computable
without another model in the loop: classification accuracy, Recall@K against the
expected document slugs, citation accuracy (the answer's cited ids), abstention
correctness, and the security outcomes (a hostile case must still be
non-executable).

Layer: tooling, pure functions over results.

The deliberate exclusion: no LLM-as-judge. A judge scoring output from its own
model family is not evidence; the deterministic metrics are enough to catch
regressions on the golden path (``docs/architecture.md`` §13).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricResult:
    """One named metric and its value."""

    name: str
    value: float
    cases: int


def recall_at_k(results: list[dict[str, object]], *, k: int = 5) -> MetricResult:
    """Fraction of cases whose expected document appears in the top-k hits. M0 stub."""
    raise NotImplementedError


def citation_accuracy(results: list[dict[str, object]]) -> MetricResult:
    """Fraction of answers whose cited ids all come from the retrieved set. M0 stub."""
    raise NotImplementedError


def abstention_accuracy(results: list[dict[str, object]]) -> MetricResult:
    """Fraction of cases where the abstain/escalate decision was correct. M0 stub."""
    raise NotImplementedError


def classification_accuracy(results: list[dict[str, object]]) -> MetricResult:
    """Fraction of cases classified into the expected category. M0 stub."""
    raise NotImplementedError


def security_pass_rate(results: list[dict[str, object]]) -> MetricResult:
    """Fraction of hostile cases where no unapproved write executed. M0 stub."""
    raise NotImplementedError


def summarize(results: list[dict[str, object]]) -> list[MetricResult]:
    """Compute every Phase 1 metric for a result set. M0 stub."""
    raise NotImplementedError
