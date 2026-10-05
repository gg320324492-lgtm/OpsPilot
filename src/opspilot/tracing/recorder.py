"""``TraceRecorder``: writes ``AgentStep`` and ``AuditEvent`` rows.

Responsibility: the single place that emits trace rows. Every model call, tool
call, retrieval, state change and approval writes through here, so the set of
recorded events is one reviewable list rather than scattered call sites.

Layer: cross-cutting (``tracing``). Writes through the store ports.

The two records are not redundant (``docs/data-model.md`` §3):

- ``AgentStep`` is the execution timeline -- ordered by ``sequence``, answers
  "what happened, in what order, how long did it take"; a UI concern.
- ``AuditEvent`` is the append-only ledger -- ``event_type``/``actor``/``payload``,
  answers "who or what did this, and was it authorised"; never rewritten.

Phase 1 stores the full prompt and response in ``agent_steps.input``/``output``
by design (``STORE_FULL_PROMPTS``), because a trace you cannot read is not a
trace. API keys are never recorded; field-level redaction of prompts is
explicitly Phase 3 and is listed in ``docs/limitations.md``.
"""

from __future__ import annotations

from uuid import UUID


class TraceRecorder:
    """Records steps and audit events for one run."""

    def __init__(self, *, run_id: UUID) -> None:
        self._run_id = run_id

    async def record_step(
        self,
        *,
        step_type: str,
        input_payload: dict[str, object] | None = None,
        output_payload: dict[str, object] | None = None,
        latency_ms: int | None = None,
    ) -> UUID:
        """Append an ``AgentStep``, allocating the next ``sequence`` atomically.

        The sequence is allocated inside the same transaction as the insert
        (``SELECT COALESCE(MAX(sequence),0)+1 ... FOR UPDATE``), so the timeline
        the dashboard renders cannot silently reorder. M0 stub.
        """
        raise NotImplementedError

    async def record_state_change(self, *, from_status: str, to_status: str) -> None:
        """Append the ``state_change`` step that must accompany every transition.

        Written in the same transaction as the ``agent_runs.status`` update. M0
        stub.
        """
        raise NotImplementedError

    async def record_audit(
        self,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, object] | None = None,
    ) -> UUID:
        """Append an ``AuditEvent``. There is no update or delete path. M0 stub."""
        raise NotImplementedError
