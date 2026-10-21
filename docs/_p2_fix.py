import pathlib

edits = [
    # MEDIUM -- the i18n leaf counts, in both places.
    (
        "docs/phase2-plan.md",
        """`web/src/i18n/dictionaries/en.json` (334 leaves) and
  `web/src/i18n/dictionaries/zh-CN.json` (330). The four-leaf difference is
  exactly the `.one` plural forms Chinese does not have:""",
        """`web/src/i18n/dictionaries/en.json` (338 string leaves) and
  `web/src/i18n/dictionaries/zh-CN.json` (334). The four-leaf difference is
  exactly the `.one` plural forms Chinese does not have:""",
    ),
    (
        "docs/phase2-plan.md",
        """`web/src/i18n/` with `en` (334 leaves) and `zh-CN` (330); the four-leaf difference is exactly the `.one` plural forms Chinese does not have""",
        """`web/src/i18n/` with `en` (338 string leaves) and `zh-CN` (334); the four-leaf difference is exactly the `.one` plural forms Chinese does not have""",
    ),
    # MEDIUM -- the installer procedure describes a nav that does not exist.
    (
        "docs/installer-verification.md",
        """| | the dashboard renders: a left-hand nav (Tickets, Runs, Knowledge, Approvals) and an empty ticket list |""",
        """| | the dashboard renders: a nav bar **across the top** (Dashboard, Tickets, Runs, Approvals, Knowledge) and a *Recent runs* panel reading "No runs yet" |""",
    ),
    (
        "docs/installer-verification.md",
        """Click **Knowledge** in the left nav. Click **Reindex knowledge**.""",
        """Click **Knowledge** in the nav bar. Click **Reindex knowledge**.""",
    ),
    # LOW -- two stale line numbers in the A1 row.
    (
        "docs/phase2-plan.md",
        """8000 (`:256`), 3000 (`:478`)""",
        """8000 (`:261`), 3000 (`:515`)""",
    ),
    # LOW -- the claim_next citation.
    (
        "docs/phase2-plan.md",
        """`src/opspilot/adapters/persistence/repositories.py:453`""",
        """`src/opspilot/adapters/persistence/repositories.py:544`""",
    ),
    # LOW -- the committed-file count.
    (
        "docs/phase2-plan.md",
        """0 of the **290 committed files** in `evals/results/` touch the holdout""",
        """0 of the **340 files** in `evals/results/` touch the holdout (six of them
      are tracked; the rest are gitignored local runs)""",
    ),
    # LOW -- what the root evals/ directory holds.
    (
        "docs/phase2-plan.md",
        """the root `evals/` holds only `datasets/` and `results/`""",
        """the root `evals/` holds `datasets/`, `results/`, `README.md` and `__init__.py`""",
    ),
    # LOW -- three images are built, not two.
    (
        "docs/installer-verification.md",
        """Docker Desktop has to build
the API and worker images from source;""",
        """Docker Desktop has to build
the API, worker **and migration** images from source (three `build:` blocks in
`docker-compose.yml`, and no shared `image:`);""",
    ),
]

for path, old, new in edits:
    p = pathlib.Path(path)
    s = p.read_text(encoding="utf-8")
    assert old in s, f"{path}: {old[:70]}"
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("patched", path)
