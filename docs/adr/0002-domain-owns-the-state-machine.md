# ADR-0002 — The domain owns the state machine; LangGraph is an executor

**Status:** Accepted (Phase 1)

## Context

LangGraph is the chosen orchestration library. The idiomatic way to use it is to
declare the workflow *as* a graph: nodes are steps, edges are the transitions,
and the library's state object holds the run.

That makes the graph the source of truth for control flow. Two problems follow.

First, the set of legal transitions becomes an emergent property of edge
declarations scattered through graph construction. `COMPLETED → EXECUTING` being
impossible is then a fact about the graph, checkable only by building the graph
and inspecting it — and a new node added in a hurry can create an edge nobody
noticed was illegal.

Second, the transition rules stop being unit-testable without the library. The
most safety-relevant rule in the project — you cannot leave a terminal state, and
you cannot reach `EXECUTING` from `WAITING_APPROVAL` without an approval row —
would be in the library's domain.

The specification also requires that replacing LangGraph later must not force a
rewrite of the business model. If the graph *is* the model, that requirement is
unmeetable by construction.

## Decision

The state machine is domain code. `src/opspilot/domain/runs.py` declares
`RunStatus`, `ALLOWED_TRANSITIONS` and `AgentRun.transition_to()`, importing only
`enum` and Pydantic. Status changes go through `transition_to`, which:

1. looks up `ALLOWED_TRANSITIONS[current]`,
2. raises `IllegalTransition` if the target is not in it,
3. returns the new state, and
4. is the only method that mutates `status` — there is no setter.

The orchestrator is a port:

```python
class Orchestrator(Protocol):
    async def step(self, run: AgentRun, ctx: RunContext) -> StepOutcome: ...
```

with `LinearOrchestrator` (a deterministic driver used by tests) and
`LangGraphOrchestrator` (production). Both call `run.transition_to(...)`; neither
decides what is legal.

The transition table is asserted against the enum in a test that iterates
`RunStatus` and requires every member to appear as a key, so adding a state
without deciding its transitions is a red test.

## Consequences

**Good.** The security-critical transition rules are tested in microseconds with
no graph, no library and no async. A future contributor adding a state is forced
to answer "what may it become" by the test rather than by remembering. Replacing
LangGraph touches `adapters/orchestration/` and nothing else. The golden-path
integration suite runs on `LinearOrchestrator`, which is deterministic, so a
model or graph upgrade cannot make integration tests flaky.

**Cost.** The domain state machine and the graph's shape can drift: the graph
might not offer a path the domain permits. This is caught by a contract test that
walks the golden path on the real `LangGraphOrchestrator` and asserts the
observed transition sequence is a subsequence of the legal set. The drift is
possible; it is not silent.

**Sequencing consequence.** The runtime and the linear orchestrator are built in
M4 before the LangGraph adapter. Building the adapter first would make the graph
the de facto design and the domain table a transcription of it — the outcome
this ADR exists to prevent.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| The graph is the state machine | Testability and library independence both lost; contradicts an explicit specification requirement. |
| LangGraph nodes that call `transition_to` but declare their own edges | Edges are still the real control flow. The table would document a graph that could already violate it. |
| A hand-rolled async driver, no LangGraph at all | Simpler, but the specification asks for LangGraph behind an abstraction, and the graph genuinely helps with Phase 2's retry and replay work. |
| Booleans (`running`, `approved`, `failed`) | The specification forbids this explicitly, and correctly: boolean combinations encode states that must not exist (`approved=True, running=True, failed=True`). |
