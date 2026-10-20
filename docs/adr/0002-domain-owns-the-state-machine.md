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
the standard library and Pydantic. Status changes go through `transition_to`,
which:

1. looks up `ALLOWED_TRANSITIONS[current]`,
2. raises `IllegalTransition` if the target is not in it,
3. returns the new state, and
4. is the only method that mutates `status` — there is no setter.

The orchestrator is a port:

```python
class Orchestrator(Protocol):
    async def step(self, run: AgentRun) -> StepResult: ...
```

with `LinearOrchestrator` (a deterministic driver used by tests) and
`LangGraphOrchestrator` (production). Neither decides what is legal: the
transition table does, and the runtime's progression goes through it.

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

**Correction 1 (partly superseded — see Correction 2).** The two sentences above
— "The golden-path integration suite runs on `LinearOrchestrator`, which is
deterministic" and "This is caught by a contract test that walks the golden path
on the real `LangGraphOrchestrator`" — describe executors that did not exist when
this correction was written. `LangGraphOrchestrator.step` and
`.build_graph` still raise `NotImplementedError`, so the second sentence remains
false and the Decision section's "Both call `run.transition_to(...)`" is still
written in the present tense over a body that calls nothing. What drove a run
then was `agents/runtime.py::_pump`, which accepted the orchestrator argument and
discarded it (`_ = orchestrator`); the golden-path harness, `worker/__main__.py`,
`evals/runner.py` and `scripts/demo_golden_path.py` each carried a
`LinearOrchestrator` without running it.

**Correction 2 (the linear executor now drives).** `LinearOrchestrator` is
implemented: the stage logic was extracted from `_pump` into one function per
`StepKind`, sequenced by a `StageDriver`, and `LinearOrchestrator` is a thin
dispatcher over it. `_pump` now binds the driver to the orchestrator and loops
`orchestrator.step`, so the first sentence above — "the golden-path integration
suite runs on `LinearOrchestrator`" — is made true by
`tests/agent/test_linear_orchestrator_drives.py`, which drives the golden path
through a spy that records the stage sequence rather than merely asserting the run
completes. `LangGraphOrchestrator` and the cross-executor contract test remain
unbuilt, so the drift named in this section is now prevented for the executor
that runs and still uncaught for the one that does not.

What this ADR argues for is unaffected, and that is why it stands. The
dependency-inversion argument is a reason to draw the boundary now and to decide
the question of executors at all: the transition table lives in `domain/` and is
unit-testable today without any graph library, and that is verified —
`tests/unit/` iterates `RunStatus` against `ALLOWED_TRANSITIONS` as the Decision
section states. Replacing LangGraph will touch `adapters/orchestration/` and
nothing else *because of where the table is placed*, which is the property this
ADR is actually about, and it holds whether or not an executor exists yet. The
contract test named above is still the right mitigation for that risk and is
still unbuilt; it is recorded as an acceptance criterion in `docs/milestones.md`
§M4, where both orchestrator rows are still unchecked.

**Sequencing consequence.** The runtime and the linear orchestrator are built in
M4 before the LangGraph adapter. Building the adapter first would make the graph
the de facto design and the domain table a transcription of it — the outcome
this ADR exists to prevent.

**Correction 3 (the table did not describe the machine the runtime runs).**
This ADR's central claim — that the domain owns the state machine — was true of
the *code* and false of the *table*, and the gap between them was invisible
because nothing compared the runtime's actual transitions with the table.

What the golden path records, read back from the `state_change` rows it wrote:

```
received → classifying → retrieving → planning → executing
→ planning → executing → planning → executing → planning → executing
→ waiting_approval → executing → responding → completed
```

`EXECUTING → PLANNING` is on that sequence **four times** — once per tool
round, because each tool is a separate plan→gate→execute round. The table did
not carry that edge. It carried `EXECUTING → EXECUTING` instead, which the
runtime never takes. So the table described a machine the code did not run, and
the two guards that existed were blind to it: `tests/unit/test_run_state.py`
compares the table against the **enum**, and
`tests/unit/test_state_machine_document_matches_the_code.py` compares the
**document** against the **table**. Both compared two artefacts that agreed
with each other, and neither with the runtime.

Nothing caught it because **no runtime transition was ever checked**. The
runtime assigned `ctx.run.status` directly, in four places, so a transition
the table forbade was written silently — and `IllegalTransition`, the
exception this ADR exists for, was unreachable in production. The two guards
that were named in this ADR as the mitigation for graph drift could not have
caught it either: they compare artefacts, not behaviour.

What changed:

- the table now carries the edge the runtime takes
  (`docs/agent-state-machine.md` §2), and the specification's prose says so;
- the runtime's **progression** goes through `transition_to`
  (`agents/runtime.py::_transition`), so an illegal progression raises
  `IllegalTransition` instead of being written — which is what this ADR's
  Decision section always said it did;
- `tests/agent/test_every_transition_is_in_the_table.py` drives the golden path
  through the real worker and asserts that **every recorded transition is an
  edge the table allows**. That is the guard the other two could not be: it
  compares the runtime's behaviour with the specification.

The Decision section's quoted `Orchestrator` protocol was stale in the same
way — it showed `step(self, run, ctx) -> StepOutcome`, and the shipped port is
`step(self, run) -> StepResult` with no context parameter, because `ports/`
cannot import `agents/`. The context is bound at run time by a `StageDriver`
instead. The quote above has been corrected.

One write remains outside the table's reach, and it is named in
`docs/agent-state-machine.md` §2.2 rather than left implicit: the park. The
failure is checked (`FAILED` is reachable from every non-terminal state, and
the table has no edge out of a terminal one, so a finished run cannot be
overwritten by an error that arrived afterwards). The park is not, for a reason
that is a product decision rather than a technical one and is recorded as open
in `docs/limitations.md` §7.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| The graph is the state machine | Testability and library independence both lost; contradicts an explicit specification requirement. |
| LangGraph nodes that call `transition_to` but declare their own edges | Edges are still the real control flow. The table would document a graph that could already violate it. |
| A hand-rolled async driver, no LangGraph at all | Simpler, but the specification asks for LangGraph behind an abstraction, and the graph genuinely helps with Phase 2's retry and replay work. |
| Booleans (`running`, `approved`, `failed`) | The specification forbids this explicitly, and correctly: boolean combinations encode states that must not exist (`approved=True, running=True, failed=True`). |
