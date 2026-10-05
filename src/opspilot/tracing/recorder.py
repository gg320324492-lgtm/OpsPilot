"""``TraceRecorder``: writes ``AgentStep`` and ``AuditEvent`` rows.

Responsibility: the single place that emits trace rows. Every model call, tool
call, retrieval, state change and approval writes through here, so the set of
recorded events is one reviewable list rather than scattered call sites.

Layer: cross-cutting (``tracing``). Writes through the persistence adapter, which
is the one thing this package is allowed to reach below the ports: the audit
ledger is append-only and deliberately has *no* mutating store port -- a port
that could update an ``AuditEvent`` would be an invitation to rewrite the ledger.
The agent runtime (``agents/``) must not import SQLAlchemy or the adapter, so it
takes a ``TraceRecorder`` and calls this instead.

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

from typing import Any
from uuid import UUID


class TraceRecorder:
    """Records steps and audit events for one run.

    Constructed per call site with the run id it is recording for, so a caller
    cannot accidentally write an event against the wrong run. The session factory
    is resolved lazily from settings on first write: importing the persistence
    adapter at module scope would make this package unimportable without a
    database URL configured, which the unit tests do not have.
    """

    def __init__(self, *, run_id: UUID, session_factory: Any = None) -> None:  # noqa: ANN401
        """Bind to a run and, optionally, a specific session factory.

        ``session_factory`` should be passed whenever the caller already has one
        -- the tests pass the same factory the repositories use, because a second
        factory over an in-memory SQLite URL would open a *different* empty
        database and the audit rows would vanish. When it is omitted (the
        production wiring, where the URL is a file or Postgres) a factory is
        built lazily from settings.
        """
        self._run_id = run_id
        self._factory = session_factory

    def _session_factory(self) -> Any:  # noqa: ANN401 -- SQLAlchemy sessionmaker
        """Return the bound session factory, or build one from settings."""
        if self._factory is not None:
            return self._factory
        from opspilot.adapters.persistence import db
        from opspilot.settings import get_settings

        return db.session_factory(get_settings())

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
        (``SELECT COALESCE(MAX(sequence),0)+1``), so the timeline the dashboard
        renders cannot silently reorder.
        """
        from sqlalchemy import func, select

        from opspilot.adapters.persistence import db, models

        factory = self._session_factory()
        try:
            with db.session_scope(factory) as session:
                highest = session.execute(
                    select(func.max(models.AgentStep.sequence)).where(
                        models.AgentStep.run_id == self._run_id
                    )
                ).scalar()
                step = models.AgentStep(
                    run_id=self._run_id,
                    sequence=int(highest or 0) + 1,
                    step_type=step_type,
                    input=input_payload,
                    output=output_payload,
                    latency_ms=latency_ms,
                )
                session.add(step)
                session.flush()
                return step.id
        finally:
            self._dispose(factory)

    async def record_state_change(self, *, from_status: str, to_status: str) -> None:
        """Append the ``state_change`` step that must accompany every transition.

        Written in the same transaction as the ``agent_runs.status`` update by
        ``RunStore.set_status``; this method exists for callers that moved the
        status some other way and still owe the trace its step.
        """
        await self.record_step(
            step_type="state_change",
            output_payload={"from_status": from_status, "to_status": to_status},
        )

    async def record_audit(
        self,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, object] | None = None,
    ) -> UUID:
        """Append an ``AuditEvent``. There is no update or delete path."""
        from opspilot.adapters.persistence import db, models

        factory = self._session_factory()
        try:
            with db.session_scope(factory) as session:
                event = models.AuditEvent(
                    run_id=self._run_id,
                    event_type=event_type,
                    actor=actor,
                    payload=dict(payload) if payload is not None else {},
                )
                session.add(event)
                session.flush()
                return event.id
        finally:
            self._dispose(factory)

    def _dispose(self, factory: Any) -> None:  # noqa: ANN401 -- sessionmaker
        """Close the engine, but only when this recorder built the factory itself.

        An injected factory belongs to its caller and must not be disposed here:
        disposing it would tear down the connection pool the rest of the test (or
        the app) is still using.
        """
        if self._factory is not None:
            return
        engine = factory.kw.get("bind")
        if engine is not None:
            engine.dispose()


__all__ = ["TraceRecorder"]
