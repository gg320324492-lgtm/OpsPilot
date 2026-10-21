import pathlib

edits = [
    # M1 -- a ticked criterion that is not met as written.
    (
        "docs/milestones.md",
        """- [x] `tests/security/` has an adversarial test per row of
      `tool-permissions.md §5`'s right-hand column.
      (Six rows, six adversaries in `test_approval_bypass.py`, whose docstring
      maps each row to its test name; each also asserts the spy dispatched
      nothing.)""",
        """- [x] `tests/security/` has an adversarial test for **six of the seven**
      rows of `tool-permissions.md §5`'s right-hand column, each also asserting
      the spy dispatched nothing.
      (The seventh is the currency row, and it is not in `tests/security/` **by
      design**: a currency never reaches the approver — it is refused at gate
      1, before any of this — so the refusal is tested where it happens, in
      `tests/unit/test_permissions.py` and
      `tests/integration/test_refund_risk_explanation.py`.
      `test_approval_bypass.py`'s docstring used to claim it implemented that
      column "verbatim", and it does not: its six items are two that the column
      does not list and three that the column does.)""",
    ),
    # M2 -- the two ci.yml line numbers.
    (
        "docs/milestones.md",
        """- [x] `alembic upgrade head` runs against a pgvector container in CI. (Job
      `verify (pgvector)` in `.github/workflows/ci.yml` declares the service
      container `pgvector/pgvector:pg16` (line 175) and its first step is
      `alembic upgrade head` (line 219), followed by `alembic current`.)""",
        """- [x] `alembic upgrade head` runs against a pgvector container in CI. (Job
      `verify (pgvector)` in `.github/workflows/ci.yml` declares the service
      container `pgvector/pgvector:pg16` (line 277), and one of its steps is
      `alembic upgrade head` (line 321), followed by `alembic current` — which
      the job now compares against `alembic heads` rather than merely printing.)""",
    ),
    # M3 -- the set_status citation.
    (
        "docs/milestones.md",
        """      state_change step, then flushes once, in one _scope —
      repositories.py:395-420; test_runtime_fake.py::test_every_state_transition_is_persisted_in_order.)""",
        """      state_change step, then flushes once, in one _scope —
      repositories.py:665-705; test_runtime_fake.py::test_every_state_transition_is_persisted_in_order.)""",
    ),
    # M4 -- the AgentStep constructor.
    (
        "docs/milestones.md",
        """      by the AgentStep(step_type="state_change", …) constructor in
      SqlRunStore.set_status (adapters/persistence/repositories.py:407-418),""",
        """      by the AgentStep(step_type="state_change", …) constructor in
      SqlRunStore.set_status (adapters/persistence/repositories.py:693-703),""",
    ),
    # M5 -- the two web citations.
    (
        "docs/milestones.md",
        """      web/src/lib/format.ts:136 ("a null here is not missing data and is not""",
        """      web/src/lib/format.ts:154 ("a null here is not missing data and is not""",
    ),
    (
        "docs/milestones.md",
        """      web/src/app/runs/[runId]/page.tsx:255 ("a 0 there would be a regression""",
        """      web/src/app/runs/[runId]/page.tsx:388 ("a 0 there would be a regression""",
    ),
    # M6 -- the already-decided mapping.
    (
        "docs/milestones.md",
        """      lib/decision-error.ts:164 maps it to tone: "conflict" with the""",
        """      lib/decision-error.ts:125 maps it to tone: "conflict" with the""",
    ),
    # M7 -- the Citations panel.
    (
        "docs/milestones.md",
        """      (`runs/[runId]/page.tsx:333-370 — the `Citations` panel maps""",
        """      (`runs/[runId]/page.tsx:486-510, rendered at :199 — the `Citations` panel maps""",
    ),
    # M9 -- the committed result files.
    (
        "docs/milestones.md",
        """both committed files""",
        """the four committed files""",
    ),
    (
        "docs/milestones.md",
        """Two live runs are committed""",
        """Four live runs are committed""",
    ),
    # M11 -- gate 2's rejection.
    (
        "docs/milestones.md",
        """(Gate 2 at `runtime.py:460` calls `_reject`""",
        """(Gate 2 at `runtime.py:452` calls `_reject`""",
    ),
    # M12 -- the two runs.py citations.
    (
        "docs/milestones.md",
        """`runs.py:53` sets `prefix="/api"`""",
        """`runs.py:54` sets `prefix="/api"`""",
    ),
    (
        "docs/milestones.md",
        """`timeline.sort(...)` (`runs.py:266`)""",
        """`timeline.sort(...)` (`runs.py:304`)""",
    ),
]

for path, old, new in edits:
    p = pathlib.Path(path)
    s = p.read_text(encoding="utf-8")
    assert old in s, f"{path}: {old[:70]}"
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("patched |", old.strip()[:60])
