"""``docs/limitations.md`` §8 must not go stale behind the code's back.

## Why this file exists

``docs/limitations.md`` §8 is *Documented but not implemented*: a list of six
capabilities the repository records as **absent** — absent because a decision
was taken, or absent because nothing was written. Each entry says which, and
names the evidence.

The failure mode this section exists to prevent is a claim that reads as
evidence of something it is not. It is therefore also the section most likely
to **go stale in the other direction**: the repository keeps adding guards, and
a §8 entry that says "nothing implements this" or "no test covers this" becomes
a lie the moment somebody implements it or writes the test — silently, because
the entry is a paragraph of prose and no machine reads it.

Every entry here was checked against the tree on 2026-11-08 and all six still
hold. That is a snapshot, and a snapshot is exactly what rots. This file is
what stops the snapshot rotting: each entry gets an assertion, so the day the
capability lands the guard goes red and the entry has to be rewritten in the
same commit that closed it.

## What this file asserts

One test per §8 entry, each naming the entry:

1. **Item 1 — ``issues.create`` is not on the golden path.** The tool exists,
   is ``SAFE_WRITE``, and the committed golden-path fixture proposes no
   ``issues.create`` call.
2. **Item 2 — the two-worker guarantee is conditional.** The claim writes the
   lease columns in the claim's own transaction, the renewal is conditional on
   the holder, and the test that asserts the *lapse* is the tested failure mode
   exists.
3. **Item 3 — ``LangGraphOrchestrator`` is implemented by nothing.** Both of its
   methods raise ``NotImplementedError``, no module imports ``langgraph``, and
   no test imports the adapter.
4. **Item 4 — a safety case's expectations are recorded, not asserted.** The
   metrics read ``expected_terminal`` and read none of the other four.
5. **Item 5 — the corpus describes controls this system does not have.** The
   approval row has no role or band, one tool call has one approval row, and
   there is no retention, purge or backup component in ``src/``.
6. **Item 6 — the corpus's anti-false-positive material has never been
   triggered.** The seeded billing data holds exactly one invoice with more
   than one transaction, and it is a real duplicate.

## What it does not do

It does not judge whether a §8 entry *should* exist — that is a product
decision, and several of these entries are recorded as deliberate. It checks
the mechanical half: that the thing the entry says is absent is still absent,
and that the thing it says is present is still present. An entry that is
closed correctly makes this file go red, which is the intended outcome: the
entry and the code change together.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import re
from uuid import uuid4

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The document whose §8 this file holds to the tree.
LIMITATIONS = REPO_ROOT / "docs" / "limitations.md"

#: The committed golden-path fixture the golden path replays.
GOLDEN_FIXTURE = REPO_ROOT / "evals" / "datasets" / "fixtures" / "duplicate_charge.json"

#: The safety dataset §8 item 4 is about.
SAFETY_DATASET = REPO_ROOT / "evals" / "datasets" / "safety.jsonl"

#: Where the metrics live — the only place a safety expectation can be read.
METRICS = REPO_ROOT / "src" / "opspilot" / "evals" / "metrics.py"

#: The billing MCP server's seed data — the "seeded database" of item 6.
BILLING_SEED = REPO_ROOT / "mcp_servers" / "billing" / "seed.json"

#: The approval model, for item 5.
APPROVALS = REPO_ROOT / "src" / "opspilot" / "domain" / "approvals.py"

#: The initial migration, for item 5's unique constraint.
MIGRATION = REPO_ROOT / "migrations" / "versions" / "0001_initial_schema.py"

#: The repositories, for item 2's lease columns.
REPOSITORIES = REPO_ROOT / "src" / "opspilot" / "adapters" / "persistence" / "repositories.py"

#: The eval runner, for item 4's "the last one is not even copied".
RUNNER = REPO_ROOT / "src" / "opspilot" / "evals" / "runner.py"

#: This file itself — excluded from the "no test imports the adapter" search,
#: because this file names the adapter in order to assert that nothing else
#: does.
THIS_FILE = pathlib.Path(__file__).resolve()


def _reads(field: str) -> bool:
    """Whether ``metrics.py`` *accesses* a field.

    Accessing is what reading looks like in this codebase: ``get("field")`` or
    ``["field"]``. The word merely appearing is not reading — ``metrics.py``
    has a sentence that begins "A note on the raw result shape", and ``note``
    is a field name.
    """
    source = METRICS.read_text(encoding="utf-8")
    return any(
        pattern in source
        for pattern in (
            f'get("{field}")',
            f"get('{field}')",
            f'["{field}"]',
            f"['{field}']",
        )
    )


def _class_fields(path: pathlib.Path, class_name: str) -> set[str]:
    """The annotated field names of one class in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
    raise AssertionError(f"{path.name} has no class {class_name!r}")


def _method_body(path: pathlib.Path, method: str) -> str:
    """The source of one ``async def``/``def`` in a file, by name."""
    source = path.read_text(encoding="utf-8")
    start = source.index(f"def {method}(")
    tail = source[start + 1 :]
    end = tail.find("\n    async def ")
    if end == -1:
        end = tail.find("\n    def ")
    return source[start : start + 1 + end] if end != -1 else source[start:]


# ---------------------------------------------------------------------------
# Item 1 — issues.create is not on the golden path
# ---------------------------------------------------------------------------


def test_item_1_the_golden_path_fixture_proposes_no_issues_create() -> None:
    """§8 item 1: the committed fixture proposes no ``issues.create`` call.

    The tool itself is real and is exercised in isolation; what the entry
    records is that the **golden path** does not call it. The fixture is the
    golden path's script, so the fixture is where that is decided.
    """
    from opspilot.domain.tools import TOOL_REGISTRY, Permission

    spec = TOOL_REGISTRY["issues.create"]
    assert spec.permission is Permission.SAFE_WRITE, (
        "§8 item 1 describes issues.create as SAFE_WRITE; it is now "
        f"{spec.permission!r}. Rewrite the entry in the same commit."
    )

    fixture = GOLDEN_FIXTURE.read_text(encoding="utf-8")
    assert "issues.create" not in fixture, (
        "the committed golden-path fixture now proposes an issues.create call, "
        "so §8 item 1 ('the run does not create a ticket') is no longer true."
    )


# ---------------------------------------------------------------------------
# Item 2 — the two-worker guarantee is conditional
# ---------------------------------------------------------------------------


def test_item_2_the_lease_is_what_a_live_claim_holds() -> None:
    """§8 item 2: the lease, not the lock, is what a live claim holds.

    The entry's boundary is a lease that lapses. Three facts have to hold for
    it to be the right boundary: the claim writes the lease columns, only the
    holder can extend the lease, and a test asserts the lapse case.
    """
    claim = _method_body(REPOSITORIES, "claim_next")
    assert "claimed_until" in claim and "claimed_by" in claim, (
        "SqlRunStore.claim_next no longer writes the lease columns in the "
        "claim's own transaction; §8 item 2's boundary is no longer the right one."
    )

    renew = _method_body(REPOSITORIES, "renew_lease")
    assert "claimed_by == worker_id" in renew, (
        "renew_lease is no longer conditional on the holder; a lease any worker "
        "could extend would not be a lease, and §8 item 2's wording depends on it."
    )

    lease_test = REPO_ROOT / "tests" / "integration" / "test_worker_claim_lease.py"
    assert lease_test.exists(), (
        "tests/integration/test_worker_claim_lease.py is gone; §8 item 2 "
        "names it as the SQLite half of the lease."
    )
    assert (
        "test_a_lease_that_lapses_mid_drive_is_the_tested_failure_mode"
        in lease_test.read_text(encoding="utf-8")
    ), (
        "the test that asserts a lapsed lease makes the run claimable again is "
        "gone; §8 item 2 says that case is asserted rather than assumed."
    )


# ---------------------------------------------------------------------------
# Item 3 — LangGraphOrchestrator is implemented by nothing
# ---------------------------------------------------------------------------


def test_item_3_the_langgraph_orchestrator_is_still_unwritten() -> None:
    """§8 item 3: both methods raise, and nothing imports the module."""
    from opspilot.adapters.orchestration.langgraph_adapter import LangGraphOrchestrator
    from opspilot.domain.runs import AgentRun

    with pytest.raises(NotImplementedError):
        LangGraphOrchestrator().build_graph()

    run = AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        model_provider="fake",
        model_name="fake",
    )
    with pytest.raises(NotImplementedError):
        asyncio.run(LangGraphOrchestrator().step(run))

    # No module imports ``langgraph``. The dependency is declared in
    # ``pyproject.toml`` and imported nowhere -- that is the entry's claim.
    for path in (REPO_ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+langgraph\b", source, re.M), (
            f"{path.relative_to(REPO_ROOT)} imports langgraph; §8 item 3 says "
            "no module in the project does."
        )

    # No test imports the adapter. This file names it, so it excludes itself.
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        if path.resolve() == THIS_FILE:
            continue
        source = path.read_text(encoding="utf-8")
        assert "langgraph_adapter" not in source, (
            f"{path.relative_to(REPO_ROOT)} now imports the langgraph adapter; "
            "§8 item 3 says no test does."
        )


# ---------------------------------------------------------------------------
# Item 4 — a safety case's expectations are recorded, not asserted
# ---------------------------------------------------------------------------


def test_item_4_only_expected_terminal_is_read() -> None:
    """§8 item 4: one of the five safety expectations is checked.

    The other four are copied onto the raw result and read by nobody, and
    ``max_autonomous_amount`` is not even copied. This file holds that split:
    the day a metric starts reading ``expected_write``, this goes red and the
    entry has to be rewritten.
    """
    declared: set[str] = set()
    for line in SAFETY_DATASET.read_text(encoding="utf-8").splitlines():
        if line.strip():
            declared |= set(json.loads(line))
    expected_fields = {
        "must_require_approval",
        "expected_terminal",
        "expected_write",
        "expected_no_write",
        "max_autonomous_amount",
    }
    assert expected_fields <= declared, (
        f"the safety dataset no longer declares {sorted(expected_fields - declared)}; "
        "§8 item 4 names five fields."
    )

    assert _reads("expected_terminal"), (
        "metrics.py no longer reads expected_terminal, so §8 item 4's 'one of "
        "them is checked' is no longer true."
    )
    for field in ("must_require_approval", "expected_write", "expected_no_write"):
        assert not _reads(field), (
            f"metrics.py now reads {field}; §8 item 4 says it is recorded and "
            "read by nobody. Rewrite the entry — and close the gap it names."
        )

    runner = RUNNER.read_text(encoding="utf-8")
    assert not re.search(r'result\["max_autonomous_amount"\]', runner), (
        "the runner now copies max_autonomous_amount onto the raw result; §8 "
        "item 4 says the last one is not even copied."
    )


# ---------------------------------------------------------------------------
# Item 5 — the corpus describes controls this system does not have
# ---------------------------------------------------------------------------


def test_item_5_the_system_has_no_amount_graded_approval() -> None:
    """§8 item 5: one approval per call, and no role or band to grade it."""
    fields = _class_fields(APPROVALS, "ApprovalRequest")
    assert not {"role", "band", "approver_role", "amount_band"} & fields, (
        f"ApprovalRequest now carries a role or band field ({sorted(fields)}); "
        "§8 item 5 says it has none, and two signatures would become possible."
    )

    migration = MIGRATION.read_text(encoding="utf-8")
    assert 'sa.UniqueConstraint("tool_call_id"' in migration, (
        "approval_requests.tool_call_id is no longer unique; §8 item 5 says one "
        "tool call has one approval row, which is what makes two signatures "
        "structurally impossible."
    )

    # No retention job, no purge, no backup component anywhere in src/.
    forbidden = re.compile(r"\b(retention|purge|legal_hold|legal hold|backup)\b", re.I)
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"{offenders} now mention a retention, purge or backup mechanism; §8 "
        "item 5 says there is none anywhere in src/."
    )


# ---------------------------------------------------------------------------
# Item 6 — the anti-false-positive material has never been triggered
# ---------------------------------------------------------------------------


def test_item_6_the_seed_holds_one_real_duplicate_and_no_false_positives() -> None:
    """§8 item 6: exactly one invoice carries more than one transaction.

    The three false-positive shapes the corpus describes -- a pro-rated seat
    change, an ACH pending plus a card fallback, a chargeback -- have no
    sample. If a false positive ever appears in the seed, the corpus's defence
    against misjudging is no longer untested.
    """
    seed = json.loads(BILLING_SEED.read_text(encoding="utf-8"))
    transactions = seed["transactions"]

    by_invoice: dict[str, list[dict[str, object]]] = {}
    for transaction in transactions:
        by_invoice.setdefault(str(transaction["invoice_id"]), []).append(transaction)

    multi = {invoice: rows for invoice, rows in by_invoice.items() if len(rows) > 1}
    assert set(multi) == {"INV-2026-384"}, (
        f"the seeded data now holds {sorted(multi)} with more than one "
        "transaction; §8 item 6 says there is one, and the false-positive "
        "shapes are still unsampled."
    )

    rows = multi["INV-2026-384"]
    amounts = {float(str(row["amount"])) for row in rows}
    statuses = {str(row["status"]) for row in rows}
    assert amounts == {129.0}, (
        f"the duplicate on INV-2026-384 is no longer two charges of $129.00: {amounts}."
    )
    assert statuses == {"charged"}, (
        f"the duplicate on INV-2026-384 is no longer two 'charged' rows: {statuses}."
    )


# ---------------------------------------------------------------------------
# The list itself
# ---------------------------------------------------------------------------


def test_the_section_still_lists_the_six_remaining_entries() -> None:
    """§8's *Remaining* list has six numbered entries, and this file covers them.

    The entries are the ones this file asserts. A sixth entry added to the
    document without a matching assertion here would be a claim with nothing
    holding it — the failure this section exists to prevent, applied to the
    section itself.
    """
    text = LIMITATIONS.read_text(encoding="utf-8")
    heading = "## 8. Documented but not implemented"
    assert heading in text, (
        "docs/limitations.md no longer has a §8; this file holds that section "
        "to the tree and has nothing to hold."
    )
    body = text[text.index(heading) :]
    remaining = body.split("**Fixed, and no longer in this list**")[0]
    numbers = re.findall(r"^(\d+)\. \*\*", remaining, re.M)
    assert numbers == ["1", "2", "3", "4", "5", "6"], (
        f"§8's Remaining list now holds entries {numbers}; this file asserts "
        "the six it knows about."
    )
