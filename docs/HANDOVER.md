# Handover: continuing OpsPilot on another machine

**Written:** 2026-11-14, on the machine the project was developed on, at the
moment everything was pushed. The code state this document describes is commit
**`14fd7be`** on `main`; this file is the one commit on top of it.

**The short version.** Everything that is this project is on GitHub. Nothing on
the old machine is needed to continue: clone, install, fill in one setting, and
the suite is green. The old machine can then be wiped completely -- the whole
`G:\OpsPilot` directory, the virtual environment in it, the Docker volumes it
created, and the local `.env` with its operator token.

---

## 1. Where it is

| | |
|---|---|
| Repository | <https://github.com/gg320324492-lgtm/OpsPilot> |
| Branch | `main` (the default; there is no other branch) |
| Commit at handover | `14fd7be` |
| Clone | `git clone https://github.com/gg320324492-lgtm/OpsPilot.git` |

`main` was 88 commits ahead of the last pushed commit when this was written,
so **`git log origin/main..main` being empty is the check that you have
everything**: a fresh clone's `main` is `14fd7be` plus this handover document.

---

## 2. What the project is, in one paragraph

A support-and-billing agent. A customer ticket arrives; the agent classifies
it, retrieves the policies that apply, and **proposes** an action. It never
executes anything itself: a proposal that would move money is parked for a
human, who sees a deterministic explanation of the risk and decides. The
decisions, the retrieval and every model call are recorded as rows, so the
timeline of a run can be read back. The billing is **simulated** -- there is no
payment processor behind it, and the README says so in its first section.

---

## 3. Getting started on the new machine

### 3.1 What you need

| Tool | Version | Needed for |
|---|---|---|
| Git | any recent | everything |
| Python | **3.12 or 3.13** (`requires-python = ">=3.12,<3.14"`) | the suite, the demo, the API and the worker |
| Node | **22** (what CI uses) | the dashboard, the e2e specs, the desktop app |
| Docker Desktop | Compose v2 | running the stack; **not** needed for the Python suite |

The Python suite runs entirely on SQLite in memory and needs **no** services,
no database and no API key. `MODEL_PROVIDER=fake` is the default and replays
the committed fixtures.

### 3.2 The three things you can do

**(a) Run the suite.** This is the one to do first -- it proves the checkout is
intact.

```bash
git clone https://github.com/gg320324492-lgtm/OpsPilot.git
cd OpsPilot
python -m venv .venv && . .venv/Scripts/activate    # Windows; on POSIX: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Expect **1347 passed, 18 skipped** (1365 collected). The skips are all
environmental and are enumerated in the README's figures section: 16
`postgres`-marked tests that need a real PostgreSQL with pgvector, and 2
skips for committed result files written before the breakdown was recorded.
**There is no placeholder skip left** -- the three M0 stubs the suite used to
carry are implemented.

Then the other three gates, all of which CI runs and all of which must be
green:

```bash
ruff check                 # bare: the configured trees, not a path you typed
ruff format --check        # bare: a gate in its own right
mypy                       # bare: the file set comes from pyproject.toml
```

**Do not pass a path to `ruff` or `mypy`.** Both are configured with a file
list in `pyproject.toml` on purpose, and a narrower command silently checks
less. `tests/unit/test_mypy_configuration_is_enforced.py` and
`tests/unit/test_the_documented_ci_jobs_exist.py` exist to keep that honest.

**(b) Run the deterministic demo.** No Docker, no API key, no database -- it
runs the whole golden path in one process and prints the trace.

```bash
python scripts/demo_golden_path.py
```

**(c) Run the whole stack.** This is what the README's quick start is:

```bash
cp .env.example .env
# set OPSPILOT_OPERATOR_TOKEN to any non-empty string in .env
docker compose up
```

The dashboard is on <http://localhost:3000>, the API on
<http://localhost:8000>. The walk-through is in the README's "Running it"
section: reindex the knowledge (expect `17 seen · 17 indexed · 98 chunks
written`), submit the README's ticket, watch it park, approve, watch it
complete.

### 3.3 The one setting that is required

`OPSPILOT_OPERATOR_TOKEN` in `.env`. Compose uses the `:?` form for it, so an
unset token stops Compose from rendering the project at all. That is
deliberate -- a stack that came up without a token would have an API that
refuses every write, and the failure would be a 401 in the middle of a
demonstration rather than a refusal to start.

**The old machine's token is not in the cloud.** It was never committed
(`.env` is gitignored) and it was a local development value, not a secret
that anything else depends on. Generate a new one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3.4 The frontend and the desktop app

```bash
cd web && npm ci && npm run lint && npm run typecheck && npm run check:types
cd desktop && npm ci && npm run lint && npm run typecheck
```

`npm run e2e` (in `web/`) runs the browser walk-through against a real
Compose stack and needs Docker; it also needs
`npx playwright install --with-deps chromium`.

---

## 4. What is on the old machine and is *not* in the cloud

Everything below is either regenerable or disposable. None of it is needed to
continue.

| Path | What it is | Needed on the new machine? |
|---|---|---|
| `.env` | the local operator token and settings | **no** -- recreate from `.env.example` |
| `.venv/` | the Python virtual environment | **no** -- `pip install -e ".[dev]"` |
| `web/node_modules/`, `desktop/node_modules/` | npm dependencies | **no** -- `npm ci` |
| `.opspilot/` | the project's own runtime scratch: the test suite's temp directory, the MCP servers' live stores, and `legacy-c-temp/` (files moved off `C:` during the cleanup) | **no** -- created on demand |
| `.opspilot-data/` | the *old* MCP store directory, before it moved under `.opspilot/data/` | **no** -- superseded |
| `.scratch/` | one-off audit scripts from earlier rounds | **no** |
| `desktop/dist/`, `desktop/release*/`, `desktop/stage/`, `desktop/test-results/` | packaging output | **no** -- rebuild with `npm run package` |
| `evals/results/*.json` (untracked) | one file per local evaluation run, growing every run | **no** -- six are tracked and committed; the rest are local runs |
| `e2e.db`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/` | caches and a stray SQLite file | **no** |
| `C:\Users\admin\AppData\Local\Temp\opspilot-*` | 2,172 directories the project used to leak onto the system drive | **no** -- fixed, and the guard `tests/unit/test_the_project_writes_only_inside_the_repository.py` keeps it fixed |

**On C: specifically:** the project's files there are gone. What remains under
`C:\Users\admin` is *the tool's*, not the project's -- Claude Code's own
session store (`%USERPROFILE%\.claude\projects\G--OpsPilot`) and its
background-task directory, plus another tool's project directory. None of it is
OpsPilot's and none of it can be moved without breaking that tool.

---

## 5. The readiness checklist for the new machine

Run these in order. If they all hold, the checkout is complete and the old
machine can be wiped.

```bash
git log --oneline -2                # the handover commit, on top of 14fd7be
git status --porcelain              # empty
python -m pytest -q                 # 1347 passed, 18 skipped
ruff check && ruff format --check && mypy   # all clean
cd web && npm run lint && npm run typecheck && npm run check:types && cd ..
cd desktop && npm run lint && npm run typecheck && cd ..
```

`git status --porcelain` being empty matters: it means no file is
gitignored-and-needed. The ignored paths are listed in §4 and none of them is
source.

---

## 6. Where the project actually stands

**Green.** The Python suite, all three static gates, the web and desktop
lint/typecheck gates, and the generated client types agreeing with the live
OpenAPI schema. The browser walk-through passes locally with Docker up (85
passed, 0 skipped, measured 2026-10-21).

**Not yet run on CI.** The last green GitHub Actions run is
[`39439825308`](https://github.com/gg320324492-lgtm/OpsPilot/actions/runs/39439825308)
at commit `f410911`, which is **behind** the commit at handover. Everything
since then is verified locally and by the suite, but **has never been executed
by GitHub Actions**. The first push to `main` after this handover is what runs
it -- so watch that run, and expect the numbers in the README's figures table
to be the ones the CI job reports rather than the local ones.

**Two product decisions are open and deliberately undecided.** They are
recorded in `docs/limitations.md` §7 rather than guessed at:

1. What happens when a **terminal** run is driven again -- park it or refuse
   it.
2. The **re-propose path** for an approval that has expired.

**One acceptance criterion cannot be closed by code.** §P2-D2: the installer
walk-through has to be run by **someone who did not build the installer**. The
script is `docs/installer-verification.md` with a Chinese companion, and the
installer is obtainable from the `desktop-package` CI job's artefact. Nothing
in the repository can substitute for the walk.

**Known boundaries, all stated in `docs/limitations.md`:** the two-worker
guarantee holds while the lease holds; the local embedder is lexical, not
semantic; SQLite is a tests-only path; the committed fixtures are hand-written
placeholders rather than recordings.

---

## 7. What the next person should read, in this order

1. `README.md` -- what the project is, and the figures table with the source of
   every number.
2. `docs/agent-state-machine.md` -- the states and the edges, and what the
   gates do.
3. `docs/api-contract.md` -- the HTTP surface and the error vocabulary.
4. `docs/tool-permissions.md` -- the permission model and the five gates.
5. `docs/limitations.md` -- what is not true yet. **Read this before changing
   anything.**
6. `docs/milestones.md` -- the acceptance criteria, and which are met.
7. `docs/progress.md` -- the append-only log. It is long; the last entries are
   the most recent round.

---

## 8. Wiping the old machine

Everything is pushed and the handover document is committed, so the old
machine's copy of the project is disposable. In order:

```bash
# 1. Stop the stack and remove the volumes it created (they hold the local
#    database and the MCP stores -- nothing that is in git).
cd G:/OpsPilot
docker compose down -v

# 2. Remove the project directory, with the virtual environment in it.
rm -rf /g/OpsPilot

# 3. If the desktop app was packaged or installed from this machine, remove
#    its artefacts too:
rm -rf ~/AppData/Roaming/OpsPilot        # Electron user data (already moved
                                           # into .opspilot/legacy-c-temp and
                                           # then deleted with the directory)
rm -rf ~/AppData/Local/opspilot-desktop-updater
```

Step 1 matters: `docker compose down -v` removes the `postgres-data` and
`mcp-data` volumes. Without `-v` they survive as named Docker volumes and
keep their rows, and the next `docker compose up` on any machine will find a
database that already holds the previous machine's runs.

**Check what is left before you delete.** The one thing worth a glance is
`G:/OpsPilot/.opspilot/legacy-c-temp/` -- it holds the files moved off `C:`
during the cleanup, and nothing else. It is 847 KB of stale temp directories
and can go with everything else.

---

## 9. Two things not to do on the new machine

**Do not commit `.env`.** It is gitignored, and it holds the operator token.
`.env.example` is the template, and `tests/unit/test_the_env_example_matches_the_settings.py`
fails if the two drift apart.

**Do not point a deployment at SQLite.** `.env.example` says it in capitals and
`docs/limitations.md` §3 says why: the API and the worker are two processes, and
on SQLite the vector store lives in one process's memory, so the worker
retrieves nothing and the run abstains while the reply still claims a
resolution. The Compose stack is the supported configuration.
