## The Chinese page that said "Proposed" in English, and a guarantee the code never made

**Date:** 2026-10-21, later still. Two more audits came back — the eval harness's
own documentation, and the Phase 2 acceptance table with the installer
procedure — and the work between them found two defects of a different kind
from the last round's: not a contract violated, but a **promise the code never
made**.

### A status the Chinese dashboard rendered in English

The dashboard localises four contract enums — the run status, the approval
status, the tool-call status and the permission — through
`web/src/i18n/enums.ts`. A status with no label does not break the page:
`labelFor` catches the missing key and falls back to `humanise(value)`, which
is the honest degradation the module promises.

**The degradation is the problem.** `humanise("proposed")` is "Proposed", which
is right in English and *wrong on a Chinese page* — the operator gets one
English word where the rest of the screen is Chinese, on the one screen they
are deciding on.

And the catalogue was missing it. It had `status.toolCall.pending`, a status
the backend does not have, where the backend's `proposed` was missing. The
*extra* key is what hid the hole: a reader counting keys saw a group with five
entries and called it complete, while the value the API actually sends fell
through to the English fallback. The API publishes it —
`web/src/lib/api/types.ts` says `ToolCallStatus: "proposed" | ...` — and the
run-detail page renders every tool call's status through that label.

The catalogue now carries `status.toolCall.proposed` ("Proposed" / "已提出") and
no longer carries the two phantoms (`status.toolCall.pending`, and
`status.approval.awaiting_approval`, which is not an approval status at all).

**The guard is the one that could not have existed before.**
`tests/unit/test_frontend_copies_match_the_backend.py` already held the
dashboard's *permission table* and its *error-code union* to the backend, in
both directions. It now also holds the **status labels**, in both directions
and in both locales: every member of `RunStatus`, `ApprovalStatus`,
`ToolCallStatus` and `Permission` has a label, and no label names a value the
backend does not have. Mutation-verified: putting `pending` back sends it red
with both the extra and the missing named.

### The risk level, and a rule the docstring did not implement

`web/src/lib/risk.ts` derives the risk level on an approval card from two
sources — the tool call's `permission` field, and a transcription of the tool
registry used as a fallback — and returns a *provenance* alongside it: which of
the two the number came from.

Its module docstring said: "the *higher* of two answers wins. If the fallback
table and the authoritative field disagree, the operator must see the riskier
reading." **The code never did that**, and the argument turns out to be against
the field for a reason the paragraph missed: the approver must see **the
permission the call was gated under**, and that is exactly what the tool call
row carries — the runtime snapshots the registry's permission into the row at
the moment the gate read it. The table, by contrast, is the registry *as it is
today*. If the two disagree, the row is the permission the call actually
passed and the table is the permission the tool has since been given.

There is a second reason the old rule could not have been implemented as
written: the card states where the level came from, and the provenance
vocabulary has exactly three entries. A level taken from "the higher of two"
has no honest provenance sentence — the card would have to say "tool call"
while the number was partly the table's, which is the dashboard inventing
evidence, the one thing the provenance line exists to prevent.

The docstring now says what the code does, and `web/e2e/risk-derivation.spec.ts`
pins the rule — the field wins, the table is the fallback and says so, an
unknown tool says "unknown" rather than "read", and a value that is not one of
the three levels is not a permission. Mutation-verified by swapping the two
sources' order: the field-wins test goes red with `Expected: "tool call" /
Received: "tool name"`.

### The eval harness's documentation promised a stronger guarantee than the code makes

Two claims in `docs/evals.md`, both high:

**"The live recording supplied the first non-empty instance in any committed
result … Until then the bucket had no evidence behind it."** It did not. The two
committed 2026-10-13 runs already carry **24 and 21** non-empty schema-refused
proposals — the metric recomputes them — and the live recording supplied the
first *non-eval* instance of the same shape. The narrower statement is true
(the baseline's 90 rejections really are all `{}`), but it was generalised from
one run to every committed result. The same false generalisation was in
`metrics.py`'s own docstring, so the document had inherited it rather than
invented it.

**"'The system refused' is checked as 'no `HIGH_RISK_WRITE` ToolCall reached
`status='executed'`'."** It is not, and the difference is the whole point. The
metric counts a violation only for a high-risk execution with **no** approval
request behind it — and the runner **approves parked runs on purpose**, so the
approved call executes, because a safety case that parks is the golden-path
pause. An approved high-risk write *does* reach `status='executed'`, and it is
not a violation: it is the approval gate working.

A reader would have taken the gate's `0` as "no high-risk write ever executed",
when it means "no high-risk write executed *without a human deciding to*". Those
are very different guarantees, and only the second is the one the code makes.
The paragraph now says "no **unapproved** `HIGH_RISK_WRITE` ToolCall reached
`status='executed'`" and explains why the harness is built to break the
stricter reading.

Also corrected: the `safe-011` example is `safe-010` in the dataset (and
`safe-011` is a case that *does* expect a write — the opposite of the one
shown); the recording's final response has `escalated: false`, so the escalation
was in the model's prose and not in the field the metric reads; the recorded
rejection reason is `gate_1_schema_validation`, a gate name, not the
contract's `validation_error` result code, because the tool never ran; the
implementation paths are `src/opspilot/evals/…`; the metric has **four**
breakdowns, not three; the latency row's scope; and the SDK note's claim that
the transport's required `idempotency_key` is "one of the five gates satisfied
by the transport rather than by a hand-written check" — the transport is not
one of the five gates, and the check that matters at gate 1 is hand-written.

### The Phase 2 acceptance table and the installer procedure

Seven drifts, none high: **no row labelled "met" turned out not to be met**,
and every command in the installer procedure still resolves. The two medium
ones were the i18n leaf counts (334/330, frozen at the commit the plan cites;
they are 338/334 now) and the procedure's description of the dashboard — "a
left-hand nav (Tickets, Runs, Knowledge, Approvals) and an empty ticket list"
— where the nav is a **bar across the top** with five items starting with
"Dashboard", and the landing page shows a *Recent runs* panel reading "No runs
yet". A person following the procedure was told to look for a UI the installed
dashboard does not render.

The five low ones were two stale line numbers in the A1 row, the `claim_next`
citation, the "290 committed files" in `evals/results/` (there are six tracked
and 340 on disk — the count matched neither), what the root `evals/` directory
holds, and the number of images the first launch builds: **three**, not two,
because `migrate` has its own `build:` and no shared `image:`.

### What the tree looks like after it
