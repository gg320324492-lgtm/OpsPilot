"""Eval harness: run the datasets and compute deterministic metrics.

Responsibility: replay the JSONL eval datasets through the runtime and score the
results. Phase 1 metrics are deterministic only -- Recall@K, citation accuracy,
abstention correctness, gate outcomes -- because an LLM judge scoring its own
model family is not evidence (``docs/architecture.md`` §13).

Layer: tooling, depending on ``agents``, ``ports`` and ``adapters``.
"""

from __future__ import annotations
