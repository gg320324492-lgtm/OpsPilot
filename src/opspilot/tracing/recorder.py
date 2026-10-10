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

**``STORE_FULL_PROMPTS`` is read here, and what it governs is narrower than this
module's history suggests.** An earlier revision of this docstring claimed that
"Phase 1 stores the full prompt and response in ``agent_steps.input``/``output``
by design (``STORE_FULL_PROMPTS``)". That was not true of the code it described:
no prompt was ever written to any trace row. Every ``record_step`` payload in
``agents/runtime.py`` is structured metadata -- ``{provider, model}``,
``{category, confidence}``, ``{count, document_slugs}``, ``{tool_name, done}``,
``{from_status, to_status}`` -- and the single verbatim model string in the whole
trace is the ``body`` of the ``response`` step.

So the honest description is what the code did: the model *response* is stored
in full, and the model *prompt* is not stored at all. :data:`_FULL_TEXT_KEYS` is
the list that makes that distinction enforceable, and
:func:`TraceRecorder.record_step` is the one place it is applied -- so a caller
added later cannot start writing prompts without passing through it. Turning the
setting off therefore withholds the reply text, which is what
``limitations.md`` records as the Phase 1 privacy liability. Field-level
redaction of what remains is Phase 3.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

#: The payload keys that carry verbatim model text, and are therefore what
#: ``STORE_FULL_PROMPTS=false`` withholds.
#:
#: Deliberately a short, named list rather than "redact every string in the
#: payload". Almost every recorded field is *not* model text and is what the
#: trace is for: ``{from_status, to_status}`` is the state machine, ``{tool_name,
#: done}`` is the plan, ``{count, document_slugs}`` is the retrieval. A blanket
#: "strip the strings" rule would empty exactly those and leave a trace that
#: cannot answer any question, which is the failure ``limitations.md`` warns
#: against in the other direction -- a trace you cannot read is not a trace.
#:
#: ``body`` is the reply the model composed; ``prompt`` is reserved for the
#: prompt itself, which no caller writes today. Naming it now means the day one
#: does, it is covered by the switch rather than by whoever remembers to check.
_FULL_TEXT_KEYS: Final[frozenset[str]] = frozenset({"body", "prompt"})

#: What a withheld value is replaced with. A key is *kept* rather than dropped:
#: ``repositories.get_customer_reply`` reads ``output["body"]`` to serve
#: ``RunDetail.customer_reply`` (``docs/api-contract.md`` §3), and a payload
#: missing the key would make that reader ``KeyError`` -- or, if it used ``.get``,
#: silently render "no reply was composed" for a run that did compose one. An
#: explicit marker says "there was a reply here and it was withheld", which is
#: the same reason ``_record_model_call`` writes ``None`` rather than ``0``.
_WITHHELD: Final[str] = "[not stored: STORE_FULL_PROMPTS=false]"


def _store_full_prompts() -> bool:
    """``Settings.store_full_prompts``, read lazily so this module imports
    without a configured environment."""
    from opspilot.settings import get_settings

    return get_settings().store_full_prompts


def _withheld(payload: dict[str, object] | None) -> dict[str, object] | None:
    """Replace full-text values with :data:`_WITHHELD`; ``None`` passes through.

    A new dict rather than an in-place edit: the caller keeps its own object,
    which is what the runtime reuses for the API response it builds afterwards.
    """
    if payload is None:
        return None
    return {
        key: (_WITHHELD if key in _FULL_TEXT_KEYS and isinstance(value, str) else value)
        for key, value in payload.items()
    }


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

        ``STORE_FULL_PROMPTS=false`` replaces the full-text values in both
        payloads with :data:`_WITHHELD` and writes the row otherwise unchanged:
        the step still exists, with its type, its latency and every non-text
        field. What is withheld is the model's own words, which is the privacy
        liability ``docs/limitations.md`` records -- not the fact that the step
        happened. A step whose text is withheld is still a step a reader can
        count, and one that reports no latency is a step that would report a
        latency of ``0``.
        """
        from sqlalchemy import func, select

        from opspilot.adapters.persistence import db, models

        if not _store_full_prompts():
            input_payload = _withheld(input_payload)
            output_payload = _withheld(output_payload)

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
        """Append an ``AuditEvent``. There is no update or delete path.

        **Not** subject to ``STORE_FULL_PROMPTS``, and that is a decision rather
        than an oversight. Every audit payload the runtime writes is structural:
        ``{provider, model, latency_ms, input_tokens, ...}``,
        ``{tool_call_id, tool_name, permission, gate, reason}``,
        ``{from_status, to_status}``, ``{failure_reason}``. None of them carries
        model prompt or response text -- ``reason`` and ``risk_explanation`` come
        from ``domain/policies.py``, which is static code by construction
        (``docs/tool-permissions.md`` §2.1), not from anything the model said.

        The one free-text field here is ``approval_requested``'s ``arguments``,
        and withholding it would be actively harmful rather than private: that is
        the exact payload a human approves (``docs/tool-permissions.md`` §3.2),
        mirrored into the ledger so the record of *what was authorised* survives.
        Redacting the evidence of an approval is not a privacy control.
        """
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
