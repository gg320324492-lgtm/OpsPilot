import pathlib

edits = [
    # T2 -- gate 1 is where the parse happens, and a bad parse is a rejection.
    (
        "docs/tool-permissions.md",
        """    # GATE 1 — schema validation
    #   The arguments were already parsed into a Pydantic model when the
    #   proposal was accepted. Invalid arguments never reach here; if they do,
    #   this is a programming error and raises.
    spec = validate_tool_call(call)""",
        """    # GATE 1 — schema validation
    #   The arguments are parsed into the tool's Pydantic model *here*, and
    #   arguments that do not parse are a **result**, not a crash: the call is
    #   recorded as a rejected ToolCall, with the reason the schema gave, and
    #   is never dispatched. (This block used to say the arguments "were
    #   already parsed" when the proposal was accepted and that invalid
    #   arguments "never reach here" -- but the proposal carries a plain dict
    #   and this call is the only place it is parsed.)
    spec = validate_tool_call(call)""",
    ),
    # T1 -- the fourth rule cannot fire on the live path.
    (
        "docs/tool-permissions.md",
        """    # GATE 4 — policy engine
    #   Deterministic business rules, and exactly four of them: the denylist
    #   (a removal of capability, checked first), the idempotency replay
    #   short-circuit, the refund ceiling, and the refund-exceeds-transaction
    #   rule.""",
        """    # GATE 4 — policy engine
    #   Deterministic business rules, and exactly **three** of them can refuse
    #   here: the denylist (a removal of capability, checked first), the
    #   idempotency replay short-circuit, and the refund ceiling.
    #
    #   "Cannot refund more than the transaction was charged for" is a fourth
    #   rule, and it is **not** one of the three: it needs the charged amount,
    #   which is a row only the billing server can read, and `RefundArgs`
    #   deliberately does not declare it -- a model that could supply the
    #   number could also suppress the rule by claiming a larger charge. So the
    #   check lives at the server, as its `amount_exceeds_transaction`, and the
    #   rule here is inapplicable.""",
    ),
    # T3 -- the adversary's real home.
    (
        "docs/tool-permissions.md",
        """| Reach `COMPLETED` while a required gate was skipped | `tests/security/test_approval_bypass.py` |""",
        """| Reach `COMPLETED` while a required gate was skipped | `tests/security/test_prompt_injection.py` |""",
    ),
    # T4 -- the row names a property the file does not assert.
    (
        "docs/tool-permissions.md",
        """| Make retrieved instructions change gate 3's permission | `tests/security/test_prompt_injection.py` |""",
        """| Make retrieved instructions cause a high-risk dispatch | `tests/security/test_prompt_injection.py` |""",
    ),
    # T5 -- the invariant queries build their own database.
    (
        "docs/tool-permissions.md",
        """`tests/security/test_invariants.py` runs these as queries against the assembled test database **after the full integration suite**:""",
        """`tests/security/test_invariants.py` runs these as queries against an **assembled** database — one the file builds per test, causing the scenarios that could violate an invariant before asserting it survived:""",
    ),
    # M8 -- the i18n leaf counts, in the milestone and in the plan.
    (
        "docs/milestones.md",
        """`en` (338 leaves) and `zh-CN` (334 — the four-leaf difference is the `.one` plural forms Chinese does not have)""",
        """`en` (337 string leaves) and `zh-CN` (333 — the four-leaf difference is the `.one` plural forms Chinese does not have)""",
    ),
    (
        "docs/phase2-plan.md",
        """`web/src/i18n/dictionaries/en.json` (338 leaves) and
  `web/src/i18n/dictionaries/zh-CN.json` (334). The four-leaf difference is
  exactly the `.one` plural forms Chinese does not have:""",
        """`web/src/i18n/dictionaries/en.json` (337 string leaves) and
  `web/src/i18n/dictionaries/zh-CN.json` (333). The four-leaf difference is
  exactly the `.one` plural forms Chinese does not have:""",
    ),
    (
        "docs/phase2-plan.md",
        """`web/src/i18n/` with `en` (334 leaves) and `zh-CN` (330); the four-leaf difference is exactly the `.one` plural forms Chinese does not have""",
        """`web/src/i18n/` with `en` (337 string leaves) and `zh-CN` (333); the four-leaf difference is exactly the `.one` plural forms Chinese does not have""",
    ),
]

for path, old, new in edits:
    p = pathlib.Path(path)
    s = p.read_text(encoding="utf-8")
    assert old in s, f"{path}: {old[:70]}"
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("patched", path, "|", old.strip().splitlines()[0][:50])
