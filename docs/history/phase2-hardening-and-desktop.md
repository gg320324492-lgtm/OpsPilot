# Progress archive — Phase 2 — hardening, desktop delivery and the audit campaign

> Split from `docs/progress.md` (the append-only log) on 2026-11-16 during
> the repository tidy-up. Content is unchanged; only the file boundary
> moved. The living index is [`docs/progress.md`](../progress.md).

## P2-A — Deployment security and baseline hardening

**Date:** 2026-10-12 → 2026-10-20. `e0b32c3` (A1, A2, A4), `5c044ec` (A3), and
the closing review's correction to A3.

### Scope

The three verified findings of `docs/phase2-plan.md` §P2-A, plus the stale
descriptions a hiring reviewer opens first. A is the P0 that gates everything
else: two of its findings are about what is reachable from the network, and the
third is about who may spend money through the dashboard.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| A1 | Nothing is reachable off the host | ✅ `docker-compose.yml` publishes three ports, all loopback: `127.0.0.1:5432:5432` (`:68`), `127.0.0.1:8000:8000` (`:256`), `127.0.0.1:3000:3000` (`:505`). `tests/unit/test_compose_settings_are_wired.py::test_every_published_port_is_bound_to_loopback` parses the mappings — short and long syntax — and fails on an entry it cannot resolve rather than passing it |
| A2 | A privileged write is refused unless it came from the dashboard | ✅ all three mutating Route Handlers call `foreignOriginRefusal`: `tickets/route.ts:42`, `knowledge/reindex/route.ts:34`, `approvals/[approvalId]/[decision]/route.ts:45`. `web/e2e/write-origin.spec.ts` drives the three handlers directly in Node with `globalThis.fetch` replaced by a spy, so "the API was not called" is an observation and not an inference from a 403 |
| A3 | The database password is configurable and the default is named as one | ✅ **and it was half-true until the 2026-10-20 review** — see below |
| A4 | No document describes the browser as calling the API directly, and none describes a capability `limitations.md` §8 records as absent | ✅ the two sentences are gone: `web/README.md` no longer says the browser "talks to the API on `:8000` directly from the browser process", nor that the six screens are "routing stubs and the nav shell". Both were introduced in `a8582eb` and removed in `e0b32c3` |

### Additional verification performed beyond the criteria

- A second pass re-derived the counts the rows assert rather than quoting them:
  exactly three published ports, all loopback; exactly three mutating routes, all
  origin-checked.
- **The 2026-10-20 review found A3 half-true, and the fix is in the tree.**
  `POSTGRES_PASSWORD` was `${POSTGRES_PASSWORD:-opspilot}` on the `postgres`
  service — configurable — but every service's `DATABASE_URL` carried **its own
  literal password**: `postgresql+psycopg://opspilot:opspilot@postgres:5432/opspilot`.
  The variable was present and overridable, and a deployment was still broken: set
  `POSTGRES_PASSWORD` and the database was initialised with the new password while
  the application connected with the old one, so every `migrate`/`api`/`worker`
  authentication failed. The README's remedy — "change the password in
  `DATABASE_URL` to match" — meant hand-editing a committed file. The password in
  every `DATABASE_URL` now comes from the same variable (`docker-compose.yml:113`,
  `:136`, `:315`), and the *host* is still deliberately not templated, because it
  names the `postgres` service on the Compose network. The property now under
  test is the **linkage**, not the presence of the variable.

### Known gap carried forward

None. A1–A4 are met, and A3's half-truth was closed by the review rather than
left as a boundary.

---

## P2-D — Desktop delivery

**Date:** 2026-10-14 → 2026-10-20. `493f1b5` (the shell), `15f939f` (the
installer), `fe4127e` (the two files their work produced), `2dc2e24` (the
navigation fix).

### Scope

`docs/phase2-plan.md` §P2-D: an Electron shell that owns the stack's lifecycle, a
Windows installer that ships a staged stack and generates its own token, and the
check that the packaged artefact has not widened the P0 that §P2-A closed. A
Desktop **Demo** by decision, not by accident: the shell packages the dashboard and
owns the local UI process, and the backend still requires Docker Desktop. Bundling
PostgreSQL, the Python environment and the worker into one installer is a different
project, and Phase 2 does not attempt it.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| D1 | The app starts the backend if it is not running, surfaces a real error when Docker is absent rather than a silent failure, and stops what it started on quit | ✅ `desktop/src/lifecycle/` holds the six modules that do it (`compose.ts`, `errors.ts`, `probe.ts`, `session.ts`, `token.ts`, `types.ts`), and none of them imports Electron, so the behaviour is assertable without a display. `desktop/e2e/shell.spec.ts` launches a real window: "opens the dashboard, keeps the renderer untrusted, and stops what it started", and "names what is missing when Docker is not installed" |
| D2 | Installed on a clean Windows machine with Docker Desktop and walked end to end, **by someone who did not build it** | ✅ **met, with its caveat stated — 2026-11-14**: the operator walked the sixteen checks in `docs/installer-verification.md` (record sheet §"Filled: the walk of 2026-11-14"); index 17/17/98 → ticket → approval gate held → refund `REF-10091 / replayed:false` reconciled against the billing store → re-approve 409 → `/ready` ok → loopback-only, `origin_missing`, `unauthorized`, no credential → quit keeps volumes → the completed run survives a relaunch. Two environmental deviations recorded there: host 8000 held by an unrelated project (the packaged compose predates `e8e7f6a`), and stale same-name dev volumes inherited on the first attempt (cleared, walk repeated) |
| D3 | The **packaged** app is checked against A1–A3 the same way the dev stack is | 🟡 **met for the artefact, partial for a running instance** — see below |

### Additional verification performed beyond the criteria

- The installer's own output was inspected rather than assumed:
  `desktop/release/OpsPilot-Setup-0.1.0-x64.exe` is 121,654,288 bytes, with the
  unpacked tree beside it. The release directory is a build output and is
  gitignored (`desktop/.gitignore:13`), so the artefact is built, not committed.
- `desktop/e2e/packaged-posture.spec.ts` inspects what the installer ships: the
  packaged compose file's loopback bindings, the absence of any `.env` or token,
  and the security policy inside `app.asar`. It does **not** launch `OpsPilot.exe`,
  and the split is stated in the spec's own docstring rather than left implicit:
  launching it spawns Docker and writes a token into `%APPDATA%`, which is the
  manual step `docs/installer-verification.md` §5 exists for.
- **Rebuilt and re-verified on this machine, 2026-11-14.** `npm ci` (clean
  `node_modules`, lockfile-resolved) then `npm run package` from
  `desktop/` — the web build, the tsc compile, `stage-stack.mjs` (20 MiB staged
  into `desktop/stage/stack/`) and `electron-builder --win --x64` all ran
  without intervention. Fresh artefact:
  `desktop/release/OpsPilot-Setup-0.1.0-x64.exe`, 119,227,780 bytes,
  SHA-256 prefix `565206f73b3ef924`, beside `win-unpacked/`
  (`app.asar` 146,166 bytes, `resources/stack/` present). The size differs
  from the previous build's 121,654,288 bytes because `nsis` recompresses the
  tree it finds; the posture suite, not the byte count, is the invariant. The
  web build read `OPSPILOT_OPERATOR_TOKEN` from the root `.env` as a
  **run-time** variable only (`web/src/lib/api/client.ts:212` reads
  `process.env` server-side; nothing inlines it into the bundle), so the build
  needed no token in its inputs and the staged tree ships none — confirmed by
  the A3 assertions below.
- `desktop/e2e/packaged-posture.spec.ts` was run against the fresh artefact
  with `OPSPILOT_PACKAGED_REQUIRED=1 npx playwright test --project lifecycle
  -g packaged-posture` (required mode: no silent skips), **6 passed**:
  the ten icon sizes in the packaged executable; every published port in the
  packaged compose file names `127.0.0.1` (A1); the packaged compose file is
  byte-identical to the repository's (A1, staleness guard); no `.env` or
  operator-token file inside the package (A3); the machine's real token appears
  in no packaged file (A3); and the security policy inside `app.asar` matches
  the source policy — `contextIsolation: true`, `nodeIntegration: false`,
  `sandbox: true` (D3's renderer-untrusted assertion). It still does **not**
  launch `OpsPilot.exe`; that stays the manual step
  `docs/installer-verification.md` §5 exists for.
- `docs/installer-verification.md` is the walk-through script, and
  `docs/installer-verification.zh-CN.md` is its Chinese translation — added
  2026-10-16, because the person who has to run the installer may not read
  English.
- **The closing review found the verification documents quoting error text the
  application never displays.** The documents told a verifier to look for "Docker
  Desktop is not installed" and "Docker is installed but not responding"; the
  window says "Docker is not installed" and "Docker is installed but not running"
  (`desktop/src/lifecycle/errors.ts`). Neither document was *wrong about the
  world* — the application was telling the truth — but a person verifying against
  the wrong literal reports a failure that did not happen, and a verification
  procedure whose expected strings are wrong manufactures a finding. The
  documents are corrected, and `desktop/e2e/error-text.spec.ts` now asserts the
  documents' quoted spans against the functions the shell actually calls, in both
  languages.
- `2dc2e24` fixed the packaged app refusing every page navigation, so choosing
  中文 did nothing. A localisation that renders but cannot navigate is not a
  localisation.

### Known gap carried forward

**D2 is not closed and cannot be closed from inside this repository.** The
acceptance is "on a clean Windows machine with Docker Desktop, install the built
package and walk the golden path end to end... by someone who did not build it".
The installer was built by the author, so running it here is self-certification and
does not meet the criterion. `docs/installer-verification.md` is the script; the
missing part is a stranger. The machine-doable half of D2 is done and repeatable:
the package builds from a clean `npm ci` and the artefact passes the packaged
posture suite (above, 2026-11-14). What remains is the person.

---

## P2-E — Portfolio delivery

**Date:** 2026-10-15 → 2026-10-20.

### Scope

`docs/phase2-plan.md` §P2-E: not code, but the part a reviewer judges first, and
the part this repository has already shown it can get wrong — `6d79fbe` claimed a CI
job that was never committed, and `8c558b1` miscounted documents. Every number in
the package must be reproducible from a file in the repository.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| E1 | README positioned as a safety-gated AI operations agent, with the simulated billing stated plainly and no implication of a real finance system | ✅ `README.md:5` positions it as "**A safety-gated AI operations agent for B2B support and billing**" (README:3 at `7ad9679`), and the first section under the title is "The billing in this project is simulated. Read this first." (README:15 today; README:13 at `7ad9679`). `billing.get_invoice`, `billing.list_transactions` and `billing.issue_refund` are named as served by the billing MCP server in this repository. **It did not say this before `7ad9679`**: the README listed "3 MCP servers (CRM, Billing, Issues)" and nothing told a reviewer those are in-repo mocks, so "Billing" and "refund" could be read as a real payment integration |
| E2 | A video, screenshots, and a case study that walk one duplicate charge from intake to the audited refund | ✅ `docs/assets/golden-path.gif` (359,981 bytes), ten screenshots in `docs/assets/screenshots/`, and `docs/case-study-duplicate-charge.md` (320 lines). The GIF is from before Phase 2 (`c989ffc`, 2026-10-08); the ten screenshots and the case study are from `7ad9679` |
| E3 | A GitHub Release with the installer | ❌ **no tag exists** — `git tag` returns nothing and `gh release list` is empty. Deliberately held back pending D2 |
| E4 | Every acceptance number in the document traced to the run that produced it | ✅ the README's figures table has a third column, "Source — verify it", and every row in it points at a file: the eval rows at `evals/results/2026-10-11T15-39-42.json` → `metrics[]`, the tool-selection repeat runs at the two 2026-10-13 files, the tool registry at `domain/tools.py::TOOL_REGISTRY` |

### Additional verification performed beyond the criteria

- The README's own figures table was checked against the run files rather than
  read: `95c8e9e` corrected it when it pointed at a run two commits stale, and
  `e906f8d` later corrected five drifted figures and eight line citations that
  pointed elsewhere.
- `7ad9679` added the sentence saying the billing is simulated in the same commit
  that fixed the containerised dashboard serving 404s — the two were found
  together, by a reviewer walking the quick start rather than reading the code.
- `07e793d` corrected the release list, which had said the tree was uncommitted.
  It is not.

### Known gap carried forward

**There is no GitHub Release, and the reason is sequencing rather than
oversight.** Publishing an unverified installer would put a download link ahead of
the test the plan says it passes first, so the tag is held until D2 has been
walked by someone who did not build it. `git tag` returns nothing and `gh release
list` is empty; the installer that would be attached is built locally and is not
committed.

---

## P2-F — Internationalisation, and the half of it that is not done

**Date:** 2026-10-14 → 2026-10-20. `651bcef` (the locale and the first
catalogue), `60cda57` (the native menu bar), `2dc2e24` (the navigation fix).

### Scope

`docs/phase2-plan.md` §P2-F, requested 2026-10-13: a locale the operator chooses,
English by default, with the API's own machine identifiers left untranslated. The
plan measured the scope before starting, and the measurement is the part worth
keeping: **246 user-visible strings, hardcoded, across eight files** (six pages
and two components), no i18n framework in `web/` at all, and **50 e2e assertions
that pin English prose** — several of them written in Phase 2 to prove that a
failure state says something true and specific rather than something generic. So
the work was never "wrap the strings in a function"; it was strings + catalogue +
locale negotiation + 50 assertion updates, and the second half is where the
defects hide.

### Acceptance criteria and outcome

| # | Criterion | Outcome |
|---|---|---|
| F1 | A fresh clone still runs in English with no configuration | ✅ `DEFAULT_LOCALE = "en"` (`web/src/i18n/config.ts`) and the locale is an explicit, sticky, operator-set cookie (`opspilot_locale`) rather than `Accept-Language` negotiation — a browser's language is not an operator's decision, and silence is a bug on a screen that governs "Approve". `web/e2e/i18n.spec.ts` asserts "a fresh browser renders English, with no cookie and no configuration" |
| F2 | A locale the operator chooses, and a catalogue for it | ✅ `zh-CN` is the first translation: `web/src/i18n/dictionaries/zh-CN.json`, 330 leaves against English's 334. The four-leaf difference is the `.one` plural forms Chinese does not have, and `web/e2e/i18n-catalogue.spec.ts` fails on any key the new file is missing, so an incomplete catalogue cannot ship silently |
| F3 | Switching locale does not change what the API speaks — the error `code`s and the trace `step_type`s are machine identifiers and must not be translated | ✅ `web/src/i18n/enums.ts` draws the line explicitly: a *label for a situation* is localised, a *quoted value an operator greps into a bug report* is verbatim in every locale. `i18n.spec.ts` asserts the "No" row in both locales |
| F4 | The audit trail stays as recorded | ✅ the ledger records the same approval row whichever label an operator was reading, and `GET /api/runs?status=` accepts the same query it always did |
| F5 | The desktop shell's messages are in the operator's language — §P2-F puts the shell in scope | 🟡 **half done** — the menu bar is localised, the shell's *error copy* is not. See below |

### Additional verification performed beyond the criteria

- 22 `web-e2e` cases across `i18n.spec.ts` (7) and `i18n-catalogue.spec.ts` (15),
  plus 16 in `desktop/e2e/menu.spec.ts`.
- The native menu bar was localised last, and it keeps **its own copy** of the
  catalogue (`desktop/src/menu-policy.ts`), because a native menu is drawn by the
  main process, which has no Next.js runtime to read `web/`'s dictionaries from.
  `desktop/e2e/menu.spec.ts` reads `web/src/i18n/config.ts` and fails when the
  locale list or the cookie name drifts, so a language added to the dashboard
  cannot leave the menu behind without a red test. The shell reads the locale
  cookie out of the session's cookie store rather than being told by the page,
  which is what makes the menu right at start-up — the operator's choice from
  last time — and not only after a switch inside the running application.
- The dictionary leaf counts were recomputed from the two JSON files rather than
  quoted: `en.json` 334, `zh-CN.json` 330.

### Known gap carried forward

**The application's own error copy is not localised, and §P2-F put the shell in
scope.** Two surfaces are English:

- the dashboard's own sentences about the transport, composed in
  `web/src/lib/api/client.ts` (`The API answered {status} on /health.`, `Could
  not reach the API at {base}…`, `The API rejected the operator token…`) and
  rendered by `web/src/components/api-status.tsx:38` in a banner that appears on
  every page. These are the *dashboard's* words, not the API's, so §P2-F's
  carve-out for "the API's own user-facing text" does not cover them; and
- every title and paragraph in `desktop/src/lifecycle/errors.ts` — the Docker
  states, the token states, the Compose states — together with the probe's
  failures, the session's shutdown messages and the bootstrap page's failure
  panel.

This is recorded in `docs/limitations.md` §7 rather than fixed, and the reason is
given there: a half-localized application is worse than a documented one, because
an operator reading a Chinese page and an English error banner has to decide
which half to believe. The fix is mechanical — a second dictionary beside
`desktop/src/menu-policy.ts`'s, and six more keys in the dashboard's — and it is a
Phase 3 decision rather than a Phase 2 one.

---

## Phase 2 closes: the audit found seven real defects, and three items still need a person

**Date:** 2026-10-20. Spans `7057bd2` (the Phase 2 plan) through `60cda57` (the
native menu bar), 41 commits, plus the closing review recorded here.

### What the audit found

Phase 2 closed the way Phase 1 did. The acceptance table was written from the
tree rather than from the work packages' prose (`c746ab0`), handed to a second
reader to falsify, and **the first pass was wrong** — C1 read "met" when the
guarantee the plan asked for is not the guarantee the code makes. That correction
has its own section above, and it is not the end of the story.

The closing review of 2026-10-20 went on to check the tree for the class of
defect this repository keeps committing: **a document reading as evidence of
something the code does not say.** It found seven real defects, and the test suite
did not find one of them — two of them it had in fact blessed: the shared
`WORKER_ID` was an accepted, documented exception in
`tests/unit/test_compose_settings_are_wired.py`, and the password test asserted
the *presence* of `POSTGRES_PASSWORD` rather than the linkage between it and the
URL that has to agree with it.

1. **A shared `WORKER_ID` default defeated the claim lease's `claimed_by` column.**
   `docker-compose.yml` shipped `WORKER_ID: ${WORKER_ID:-worker-compose}` — one
   value for a whole deployment, and therefore one value for every replica of the
   worker service. But `agent_runs.claimed_by` is the second column of the lease,
   and `SqlRunStore.renew_lease` is a conditional UPDATE on it — `WHERE id = ? AND
   claimed_by = ?` (`repositories.py:552-553`) — for exactly the reason
   `migrations/versions/0002_claim_lease.py` states: a worker whose lease expired
   and whose run was stolen must not be able to extend the thief's lease. A shared
   identity makes that condition true for the wrong worker, and the lease stops
   excluding anybody. `docker compose up --scale worker=2` gave two processes one
   identity. The default is now empty and the worker derives
   `worker-<hostname>-<pid>` itself: the hostname is the container's, which Docker
   makes unique per container, and the pid keeps two processes on one host apart.
   `tests/unit/test_worker_identity_is_unique.py` pins the property, including
   that the compose fallback is empty rather than a name.

2. **A stale comment in `src/opspilot/worker/__main__.py` asserted the opposite,
   and so hid the first.** The docstring on `resolve_worker_id` said "`SqlRunStore
   .claim_next` ignores `worker_id` today -- mutual exclusion is the row lock, not
   the identity", and quoted "the row's holder is the open transaction" — a
   phrase that no longer exists in `repositories.py` at all; it described the
   pre-lease implementation. The one place whose job was to catch a shared
   identity was reassuring itself with a statement about a version of the code
   that had been replaced. The docstring now states the real reason and quotes
   the migration that says it.

3. **`POSTGRES_PASSWORD` and the password in `DATABASE_URL` were not linked, so a
   deployment that set one broke every authentication.** The database was
   initialised with the new password and the application connected with the old
   one, so every `migrate`/`api`/`worker` authentication failed, and the README's
   remedy meant hand-editing a committed file. The variable was *present* and
   *overridable* — which is precisely why A3's row read "met". Fixed; see the
   P2-A entry above.

4. **The README told a stranger `.env` was optional, and Compose refuses to render
   the project without a token.** `docker-compose.yml` uses the `:?` form for
   `OPSPILOT_OPERATOR_TOKEN` in four services, so `docker compose up` fails with
   "required variable OPSPILOT_OPERATOR_TOKEN is missing a value" before a single
   container starts. The quick start now copies `.env.example` and names the
   variable. `tests/unit/test_readme_quick_start_matches_compose.py` asserts the
   README and the compose file agree, and reads the required set out of the
   compose file rather than maintaining it by hand — so the next required setting
   is a red test rather than a stale sentence.

5. **A newly-introduced falsehood in `web/README.md`.** `e0b32c3` wrote "It never
   calls the API on `:8000` from the browser" into the same paragraph as "Reads go
   direct, and the API's CORS policy (`docs/api-contract.md` §8.1) is what makes
   those work" — and the two contradict each other. The sentence is now scoped to
   writes.

6. **The verification documents quoted error text the application never
   displays.** "Docker Desktop is not installed" and "Docker is installed but not
   responding" are strings the window has never shown; it says "Docker is not
   installed" and "Docker is installed but not running". A verification procedure
   whose expected strings are wrong manufactures a finding.
   `desktop/e2e/error-text.spec.ts` now asserts the documents' quoted spans
   against the functions the shell calls, in both languages.

7. **The dashboard's own error copy and the desktop shell's messages are
   English-only, while §P2-F put the shell in scope.** The menu bar is localised;
   the sentences an operator reads when something breaks are not. This one is
   recorded rather than fixed, in `docs/limitations.md` §7, with the reason given
   there.

### The score did not move

**The Definition of Done is 18 of 19, unchanged.** D19a stays unmet for the
reason it states. Phase 2 moved no eval metric: the committed baseline is still
`evals/results/2026-10-11T15-39-42.json`, at `tool selection 0.200 (3/15)` and
`unsafe execution count 0` — the property being claimed, and a gate. The only
*committed* live runs since are the two `tool_selection` runs B2 was deferred on,
and they were the evidence for the deferral rather than a change to anything.

### What went wrong, since the rule is to record it

- **The acceptance table's first pass was wrong, and it took a second reader to
  catch it.** C1 read "met" against a `limitations.md` that had already called the
  end-to-end promise open. A summary assembled from intent is exactly the
  document that will drift.
- **A3's row was half-true for the same reason, and nobody caught it in a
  month.** The row cited a test that existed, and the test asserted the *presence*
  of a variable rather than the *linkage* between two values that must agree. A
  citation is not a reading.
- **A stale docstring is a defect with a long fuse.** It was not merely wrong; it
  was wrong in the direction that made the real defect look fine, and it was
  written in the one place whose job was to catch exactly that.
- **The B2 section in this file asserted two things that cannot both be true.**
  "The two runs proposed an identical tool set for 8 of the 15 cases" and "every
  case that failed in both proposed different things the second time" were both
  in the same bullet, and the second was false: 6 of the 12 that failed in both
  proposed the identical set again, and 3 of those proposed nothing at all in
  either run. The section's conclusion was right and its supporting sentence was
  not, and the arithmetic hanging the deferral on the one-case spread was off as
  well. The B2 section above has been corrected against the result files; the
  conclusion has not changed.

### Where Phase 2 ends

**D2 closed on 2026-11-14** (see "D2 closed: the installer walked" below); two
items remain open, and **only a person can close any of them**:

- ~~**D2 — the installer walk-through.**~~ **Closed 2026-11-14.** The operator
  walked all sixteen checks in `docs/installer-verification.md` (record sheet
  §"Filled: the walk of 2026-11-14"): index 17/17/98, ticket, approval gate
  held, refund `REF-10091 / replayed:false` reconciled against the billing
  store, re-approve 409, `/ready` ok, loopback-only ports, `origin_missing`,
  `unauthorized`, no credential in the package, quit keeps the volumes, and
  the completed run survives a relaunch.
- **E's GitHub Release — closed 2026-11-14.** The installer was held until D2
  was walked; **D2 passed the same day** and the verified
  `OpsPilot-Setup-0.1.0-x64.exe` (119,227,780 bytes, SHA-256 `565206f7…`) was
  uploaded to the existing `v0.1.0` release, whose notes were rewritten to
  describe the asset, the verification and the two environmental caveats. What
  remains from E is the walk-through **video** §P2-E asks for — the walk itself
  was not recorded, so that item stays a stated gap rather than a ticked box.
- **C1 — the two-worker guarantee is boundary-stated-and-tested, not closed.** The
  lease closes the live-worker half: the 30/30 concurrent double-claim is gone.
  The repository asserts the opposite case in the other half — a run whose lease
  lapses mid-drive *is* claimed by a second worker, "two workers driving one run.
  That is the honest cost of a lease" — and `docs/limitations.md` §8 item 2
  lists the end-to-end promise as open. The honest state is the one the
  acceptance table now reads: the boundary is stated and tested.

Two further boundaries are recorded rather than ticked, and neither is waiting on
anybody: **B2**, deferred on measured run-to-run variance, with its own section
above; and **P2-F's error copy**, in `docs/limitations.md` §7.

CI was green when Phase 2 closed — run `39306904131`, commit `3da3a9f`, **15 of
15** jobs, where the 15 are the 14 job ids with `test` expanded across its
3.12/3.13 matrix (one id, two runs). The closing review's fixes are no longer in
the working tree: they are committed — the four deployment defects in `34904dd`,
the two previously-unreached checks in `6c55d8d`, the verification-doc text fix in
`7cc7c38`, the closing record in `0eaf4b7`, and the lint-gate fix for the new test
files in `3da3a9f` — and `39306904131` ran against that very tree, so this run is
evidence about the commits that carry those fixes and not merely about `60cda57`.
The earlier run `39299905779` at `60cda57` was 14 of 14 but predates the
`desktop-package` job the closing review adds, and a green conclusion that does not
say which tree it was checked against is one nobody can re-check.

## The app's own icon, and the two checks that were wrong about it

**Date:** 2026-10-20. `9affecd` (the icon), `1a37655` (the two guards), `07ef46e`
(the description, corrected where it contradicted itself).

### The icon the shell was borrowing, and the one it owns now

The desktop shell shipped the dashboard's favicon. `desktop/electron-builder.yml`
pointed `win.icon` at `../web/src/app/favicon.ico` — a 25,931-byte file carrying
four sizes, 16, 32, 48 and 256 — and that file was never the app's face; it was
carried across from the web tree because inventing a second asset for the
installer was the alternative. It now points at `assets/icon.ico`, which was a
146,728-byte file with ten sizes, generated by `desktop/scripts/make-icon.py`
from `docs/assets/icon-source.png`, a 1024×1024 export of a hand-drawn neon
mark set on a dark blue rounded square. Ten sizes rather than four because
Windows picks an icon's size by what it is drawing rather than by asking, and
scales up whatever is missing; 40 is not one of the shell's standard sizes and
is in the set because a size Windows asks for and does not have is a blurry
render, while one it never asks for costs a few kilobytes.

Two things about turning that export into an `.ico` are worth naming, because the
first one was wrong before it was right. The export is fully opaque — its alpha
channel runs 255 to 255 across all 1,048,576 pixels — and it was exported on a
white ground, so the ground has to be cut away before the badge can be an icon
at all. It is cut by flooding from the edge of the canvas rather than by a
threshold, because white *around* the badge is reachable from the edge and white
*inside* the artwork is not, and connectivity is the only thing that tells the
two apart; a threshold alone would take the badge's own white with it. What is
left is then given an alpha by each pixel's distance from white, and the two
constants that decide where the edge is come from measuring that artwork: the
ground beside the badge is flat white with a noise of 3 units, the badge's
antialiased edge is 51 units from white on its first pixel and 255 in the
saturated blue, and the boundary between "this is ground" and "this is the edge"
has to fall in the band between them — `GROUND_TOLERANCE = 24` and
`EDGE_WIDTH = 64`. The second thing is the resize. An RGBA resize works on each
channel on its own, the ground is white, and so the white on either side of the
badge's edge gets averaged into the edge itself: **the first version of the icon
had a white rim around the badge on a dark taskbar.** The fix is to premultiply
the alpha before resampling and undo it afterwards, so that a transparent pixel
has no colour to contribute.

The icon is committed rather than produced at package time, and the reason is
which side of the line the file falls on. The repository's root `.gitignore`
ignores `build/` by bare name, so the rule reaches `desktop/build/` as well and
an icon written there could not have been committed at all; `desktop/.gitignore`
is more exact about what its own rules are for — "Build outputs of desktop/, not
source" — and of the four directories it lists, `/stage/` and `/release/` are the
two that are large and reproducible from a checkout. The icon is neither. It is
artwork somebody maintains, and the packager reads it rather than shipping it:
the `files` whitelist is `dist/**/*` and `package.json` and nothing else, so the
`.ico` never reaches the installer's file tree at all — it is compiled into the
exe's icon resource. Generating it during the build would have put a Python
process and Pillow between a checkout and an installer.

**The container is now the script's own, and the frames are encoded better than
Pillow's writer encodes them.** Pillow's ICO writer cannot be told how to
encode the frames: its `_save()` opens a fresh buffer for each frame and calls
`frame.save(image_io, "png")` with no arguments at all, so every frame came out
at the PNG encoder's defaults — `compress_level=6`, `optimize=False` — and there
was no parameter a caller could pass to change that. `optimize=True` is worth
having, and it is worth more than a higher compression level: on the 256-pixel
frame the default comes out at 80,707 bytes, level 7 at 77,475, level 9 at
75,678, and level 6 with `optimize=True` at 75,186. It is lossless, because a
PNG's row filters are part of the format rather than a compression of it, so a
different combination of them is different bytes and the same picture, and every
decoder reads every combination. So `scripts/make_assets.py` writes the `.ico`
itself — six bytes of `ICONDIR`, sixteen bytes of `ICONDIRENTRY` per frame, and
the PNG frames — and the icon is 138,716 bytes rather than 146,728, 5.5%
smaller, with the pixels checked after the write rather than assumed: the file
is read back and every frame is compared with the frame that went in, so a
container that is well formed but holds the wrong pictures fails the build
instead of shipping. Pillow still reads the icons back; only the writing moved.

**And the palette was tried, and it does not survive the tree.** A palette
PNG — 256 indexed colours, 11,079 bytes for the 256-pixel frame against
75,186 for the RGBA one — is the obvious next step, and it was tried: the
files came out 46% and 70% smaller. It was reverted, and the reason is not the
banding, which is visible and could be argued about either way, but that
**the quantisation is not reproducible across platforms**. The same Pillow,
13.0.0 on both, quantises the same frame differently on Windows and on
Linux: the icon committed here is 74,609 bytes and the one the same script
produces on a Linux runner is 74,602, and the 256-pixel frame's pixels
differ. The guard in the `lint` job therefore fails on a tree nobody
changed — which is the failure this repository's whole arrangement of guards
exists to prevent, and the reason a committed artefact has to be something
the tree can derive on any machine that checks it. A palette frame also costs
the picture its smoothness: the deep blue field takes 5,947 distinct colours
in the RGBA frame and 78 in the palette one, and the banding that follows is
visible at one to one on a dark background.

**And the way that was found out is a container.** The drift was found by
the CI on Linux and reproduced locally in `python:3.12-slim`, and the two
disagreed — which is how a Windows workstation and a Linux runner disagree.
An artefact committed to the repository is checked by CI on Linux and usually
built on Windows, so that is the pair that has to agree, and a change to a
generated artefact is worth checking in both before it is pushed rather than
after.

### The two guards

A committed artefact is exactly the kind of file that drifts: the artwork
changes, the icon does not, and nothing says so, because a binary is not
something a diff shows. Two things could be wrong — the icon file could stop
being what the artwork makes, and the executable could stop carrying the icon
file — and before `1a37655` nothing was watching either.

**The drift guard** is `make-icon.py --check`, which regenerates the icon into a
temporary directory and compares it with the committed one; the `lint` job runs
it as a step named "The committed icon is current with the artwork". It
deliberately does not write the icon it is checking, because a check that
repairs the tree it is checking is not a check: in CI the working tree comes
back clean, and the failure becomes invisible. The precedent is the dashboard's
generated client types — `web/scripts/generate-types.mjs --check`, asserted by
`tests/integration/test_web_client_types.py` — and
`tests/unit/test_desktop_icon_is_current.py` follows it in the way that
matters, by calling the builder's own `--check` mode rather than reimplementing
the comparison, because a guard that compares with its own logic tests the
guard's logic and not the artefact.

**The packaging guard** is `desktop/e2e/packaged-posture-icon.spec.ts`, run by
the `desktop-package` job on the Windows runner that builds the artefact. It
reads the built `OpsPilot.exe` and asserts four things in increasing order of
what they would catch: the committed `.ico` carries the ten sizes; the
executable's PE resource directory has an `RT_GROUP_ICON` naming exactly those
ten; every one of them is backed by an `RT_ICON` resource holding the committed
icon's own bytes; and Windows itself — `ExtractAssociatedIcon`, the call the
shell makes — hands back the committed 32-pixel frame pixel for pixel. The size
set alone would not have caught the symptom worth catching, an icon that is the
right ten sizes and the wrong picture, and the bytes are the right comparison
here for a reason the other guard cannot use: both sides are the same committed
file, so nothing is re-encoded and nothing can drift between them. The count
assertion in that job went from five packaged-artefact checks to six, and
`.github/workflows/ci.yml` says why — a renamed file would leave five checks,
and five is not six.

### Two defects the work turned up on the way

**The `--check` argument contract was wrong, and the wrongness was silent.** The
first version of the mode inherited the build mode's `source, target` order, so
`--check some.ico` treated the icon as the *artwork*: it regenerated the
committed icon from that file and then reported the committed icon as stale — a
plausible-looking conclusion about a file the user never named, with the real
complaint about the wrong file. The mode now takes one path, the icon to
compare, with the artwork fixed to the one the tree owns, and refuses a second
path with a usage error rather than acting on it; the unit test asserts that
refusal, because a check that can be pointed at its own source can be made to
pass against the wrong icon.

*Where that fix lives, since the commit messages are not a reliable guide to
it:* the argument contract was already in this shape in `9affecd`, and
`1a37655`'s own message credits itself with correcting it. The tree is the
truth; the message is one commit out on that point and on no other. The
byte-comparison fix below, by contrast, is `1a37655`'s, and `1a37655` says so.

**The byte comparison was wrong, and wrong in a way that would have failed on
CI.** An `.ico` holds PNG frames, and what a PNG encoder writes for a given
image depends on the compressor: the same 256-pixel frame at compression levels
6, 7 and 9 comes out as 80,707, 77,475 and 75,678 bytes. Pillow changed the
deflate parameters between 11.0.0 and 11.1.0 — the same artwork, the same zlib
1.3.2, the same ten sizes, produced 143,655 bytes under Pillow 11.0.0 and
146,728 bytes under 11.1.0 and under 13.0.0, when Pillow's own writer packed
it — and the Windows and manylinux wheels bundle different zlibs. The bytes of
an icon therefore depend on what packed it, and a byte comparison calls an
unchanged tree stale on a runner whose zlib is not the one that packed the file;
worse, its remedy — regenerate the icon — changes the file and not the problem.
The decoded pixels are the same across every Pillow from 10.0.0 to 13.0.0 and on
both platforms, and that is measured rather than assumed: the ten frames of an
11.0.0 build and the ten frames of a 13.0.0 build are identical once decoded,
and the 11.0.0 and 11.1.0 builds are identical too. So `--check` compares
pictures, and `pillow` is floored at `11.1` so that a developer regenerating the
icon cannot produce a different binary from the same pictures. The tree's icon
is not packed by Pillow's writer at all any more — see the section above — but
the argument is the same one, and it is why the check still compares pictures
rather than bytes.

### The wordmark, and the gap it had

`docs/assets/logo.png` is the same badge beside the word "OpsPilot", a
horizontal lockup that the root `README.md` now carries. It was exported on a
square canvas with the lockup across the middle — a field of white with a line
of type on it — and the whitespace was cropped away, leaving 229,264 bytes at
958×289. The white is left in on purpose: a transparent wordmark would put its
dark half on a dark background, and the name is the part that has to be
readable. **This was the one asset in the set that had neither a source nor a
guard.** The icon had an artwork in the tree, a script that turns it into the
artefact, and two checks that say the artefact still matches; the wordmark was
a committed binary that nothing in the repository could regenerate and nothing
could check. That is the same species of drift the two guards were written
for, and it was open until `c5d91a8` closed it: the source is
`docs/assets/logo-source.png`, the same script crops it, and the guard now
answers for it as well.

### An environment fact, recorded because it will come up again

On this machine `npm run package` fails inside `desktop/release/`.
electron-builder deletes the old tree before it writes the new one, and
`release/win-unpacked/resources/app.asar` is held open by the editor host's own
file watcher: the Windows Restart Manager names one holder for that file, and
opening it with no sharing at all fails with ERROR_SHARING_VIOLATION (32). The
way round is to put electron-builder's output outside the workspace —
`--config.directories.output=...` — where nothing is watching it. CI's runner
has no watcher, so the job is unaffected, and this is a fact about the machine
rather than about the package.

### What was checked, and against which tree

Run `39316991857`, at `07ef46e`, is the first run of the tree that carries the
icon, and it is green at 15 of 15 — the fourteen job ids with `test` expanded
across its 3.12/3.13 matrix. Its two icon jobs are the ones that matter here:
`lint (ruff)` passed the "The committed icon is current with the artwork" step,
and `desktop-package` passed "Check the packaged artefact against A1–A3, and
against the icon it ships" together with "Assert the check really ran", which is
the assertion that the six checks were six and not five skips.

The same three facts were checked by hand against the artefact built here.
`desktop/release/win-unpacked/OpsPilot.exe`, 245,597,184 bytes, has a
`RT_GROUP_ICON` naming the ten sizes with an `RT_ICON` behind each of them;
`ExtractAssociatedIcon` on it returns a 32×32 that differs from the committed
32-frame in zero pixels; and the NSIS installer
`desktop/release/OpsPilot-Setup-0.1.0-x64.exe`, 122,076,566 bytes, carries the
same icon with the same zero difference.

`07ef46e` is the third commit and it is about the prose, because the prose
around the icon was written in one pass and reviewed in another, and the review
found that the description of the icon was not always a description of the icon:
`electron-builder.yml` said the icon "is artwork, not something the code
derives" in the same comment block that said `make-icon.py` packs the artwork
into it and is the one command that regenerates it; two files gave two accounts
of what the ten sizes are for and neither was right, with 32 and 48 called the
shell's "small and medium views" in one place and "small, medium and large" in
another and 40 unaccounted for in both; and "badge" meant the cyan mark in the
README and the whole dark square in the builder's docstring, so one word carried
two meanings in the same set of files. The repository keeps meeting this shape
of defect — a description that asserts nothing against the thing it describes —
and the description is only fixed by somebody reading it against the artefact.

### The same badge in the browser tab, and how one guard tells two icons apart

The dashboard's browser tab was showing Next.js's own template placeholder — a black disc with a white triangle, 25,931 bytes packed at 16, 32, 48 and 256 — while the shell's own icon and the installer built from it had been carrying the badge since `9affecd`, and the reason was an omission rather than a decision: the artwork was in the tree at `docs/assets/icon-source.png` and there was a generator to build an icon from it, and the favicon was simply not one of that generator's outputs. `web/src/app/favicon.ico` became that same badge, cut out of the same export the same way the shell's icon is cut and resampled the same way it is resampled, and its four frames were the shell icon's own 16-, 32-, 48- and 64-pixel frames one for one — the same decoded pixels rather than the same bytes — so the two artefacts are one picture at the sizes their consumers ask for. The file was 17,508 bytes against the placeholder's 25,931.

It was packed at four sizes at first — 16 for the Windows tab strip and the bookmark bar, 32 for the macOS tab and the macOS bookmark, 48 for Windows' medium icon view, and 64 for a high-density screen — and not the ten the shell asks for, and that difference is the whole of what separates the two files. It had no 256-pixel frame, because a 256-pixel frame is the slot a PWA's large icon goes in, this is not a PWA, and a frame nothing will ever ask for is committed binary that only has to be kept in step with the artwork; the placeholder spent 7,821 of its 25,931 bytes on that frame. The 256 was added back, and the reason it was left out turned out to be the reason it was put back: a tool that opens an `.ico` and wants one picture out of it is handed the largest frame, and the largest frame is what a high-zoom or large-icon view asks for as well. The favicon was 98,231 bytes against 17,508 without it — 5.6 times as much — because an `.ico` is fetched whole: a browser does not ask for the one frame it is going to use, and the 256 frame's 80,707 bytes of PNG arrived whether the browser wanted that size or not. Next.js's metadata image loader writes the largest frame into the link's `sizes`, so the page now announces `sizes="256x256"` where it used to announce `sizes="64x64"`. The frame nobody asked for is the frame a reader that asks for one picture gets, and what it cost was about 80 KB on the first visit to a domain and nothing after that.

This is the artefact of the three that needed the guard most, and the reason is the mechanism that reaches it. Next.js's App Router takes the file by convention alone: `app/favicon.ico` is discovered by where it sits and is injected into the page as a `<link rel="icon">` without anything in the dashboard's source naming it — a grep for `favicon` across `web/src/` comes back with nothing, and there is no import to follow, no prop to pass, and no compiler that would notice the file was three artwork revisions stale. The shell's icon at least has `win.icon: assets/icon.ico` in `desktop/electron-builder.yml` pointing at it, and the wordmark has the root README's `<img>` tag for `docs/assets/logo.png` pointing at it, and both of those are references a reader can follow and a reviewer can check; the favicon had neither, so the only thing that could have said "this is out of date" was a guard, and a guard is what it now has.

The generator moved with it, from `desktop/scripts/make-assets.py` to `scripts/make_assets.py`, and the reason is the same fact seen from the other side: it now produces an asset for the dashboard as well as one for the shell, and a generator that produces assets for the whole repository does not belong to one of the trees it produces them for. The repository's root `scripts/` was already where the Python tools live — `demo_golden_path.py`, `openapi_schema.py` and `record_live_fixture.py` are all there and all in snake_case, which is also why the file's name changed from kebab-case to snake_case on the way, because a script that sits beside those three should be named like them. Every reference to the old path moved with it, and there were five kinds of them: the ruff per-file-ignore key in `pyproject.toml`, which now reads `"scripts/make_assets.py" = ["T20"]` and would have failed the lint gate with thirty-one T201s on the script's `print` calls had the key been left pointing at the old path; the `run:` line of the "The committed artwork is current with its source" step in the `lint` job in `.github/workflows/ci.yml`, which is now `python scripts/make_assets.py --check`; `desktop/README.md`, which documents the command that regenerates each artefact and now documents the favicon as well; a comment in `desktop/electron-builder.yml` that still named the old path, which was a path to a file that no longer existed; and the `_BUILDER` constant in `tests/unit/test_desktop_icon_is_current.py`, which is now `_REPO_ROOT / "scripts" / "make_assets.py"`.

The guard now answers for two `.ico` files in one mode, so how `--check` tells them apart had to be settled before anything else, and two of the three obvious answers are wrong. It cannot be by their pixels: the favicon's frames are the shell icon's frames, one for one, so a comparison that asked "is this the badge?" would accept either file and answer a question nobody asked. It should not be by their file names either, because the guard has to work on a copy in a temporary directory — the unit test builds an icon and a favicon into `tmp_path` and points the check at each of them, and a copy's name is the test's choice rather than the artefact's identity, so a check that read the name would be asserting that the test had happened to name the file the way the script expected, which is a fact about the test and not about the artefact. What is left is the set of sizes, because the sizes are the artefact's contract with whatever consumes it: five frames is the favicon, ten is the shell's icon, and the sizes are readable out of the directory at the head of the `.ico` without decoding a frame, so a file whose pixels are damaged is still recognisable as the icon it is. A file that is neither is refused rather than guessed at, and the refusal says what the check could not decide instead of comparing the file against the wrong asset, because a check pointed at the wrong asset is a check that proves nothing.

What Next.js actually did with the file is worth recording, because "the file convention picks it up" is a claim about a mechanism and the mechanism leaves evidence. The built page carries `<link rel="icon" href="/favicon.ico?favicon.1kyzjte3kj2nl.ico" sizes="64x64" type="image/x-icon"/>`: Next reads the largest frame of the multi-size container to fill in `sizes`, and the browser then picks the frame it wants out of the container for itself, which is why the attribute says 64 and not 16. The hash in the URL is a second and independent witness, and putting the old placeholder back and rebuilding changed both of them at once — the hash went from `1kyzjte3kj2nl` to `2vob68tjqpejf` and the `sizes` from `64x64` to `256x256`, the latter because the placeholder's largest frame was 256 — and a page that was serving the file it had been built from could not have kept the same URL and declared the same size across two different files. That is the difference between a page serving this file and a page carrying a link to something.

The guard's shape changed with the artefact. `--check` with no path now checks all three of the tree's artefacts — the shell's icon, the favicon and the wordmark — and says which one it is talking about, because a check that reports "something is stale" against three candidates leaves the reader to guess, and the failure this exists to catch is precisely a failure nobody can point at. The unit tests in `tests/unit/test_desktop_icon_is_current.py` went from eleven to sixteen, and the five new ones pin the decisions above: that the committed favicon is current with the artwork, that a favicon the artwork does not generate is caught, that the check tells the shell's icon from the favicon, that the favicon carries the sizes a browser asks for, and that it is a badge and not a white square. The one that already existed — that a check with no path covers every artefact — was extended from two artefacts to three, and it now fails with a message that says the favicon is still an orphan if the guard stops reporting on it.

**The favicon got the same container, and the same saving.** The script packs the `.ico` itself rather than handing the frames to Pillow's writer, and encodes every frame with `optimize=True`; the reason is in the icon section above. The file is 92,226 bytes rather than 98,231 — 6.1% smaller — and the 256 frame is 75,186 bytes rather than 80,707, so the extra download on the first visit to a domain is about 75 KB rather than the 80 KB this section records, and the file is 5.3 times the 17,508 bytes the four-frame favicon was rather than the 5.6 times recorded above. The pixels did not move: the 256 frame is the same 262,144 bytes of RGBA it has always been, every frame of both `.ico` files is byte for byte the frame that was there before, and the guard still compares pictures rather than bytes, so a tree that regenerates the icon on a different zlib is still current.

### The badge on an iOS home screen, and the four corners that had to be filled

The dashboard had a favicon for the browser tab and nothing at all for the phone: `app/apple-icon.png` did not exist, so "add to home screen" put a screenshot of the page on the home screen instead of the badge. The chain the artwork feeds was three artefacts long — the shell's `desktop/assets/icon.ico` at 146,728 bytes and ten sizes, the dashboard's `web/src/app/favicon.ico` at 17,508 bytes and four, and the root README's `docs/assets/logo.png` at 958×289 and 229,264 bytes — and it is four now, with `web/src/app/apple-icon.png` at exactly 180×180 and 38,678 bytes generated by the same `scripts/make_assets.py` out of the same `docs/assets/icon-source.png`. It needed the same guard for the same reason the favicon did: Next.js's App Router finds the file by where it sits, so nothing in the dashboard's source names it, and an artefact nobody refers to is an artefact nobody notices has drifted.

**The shape iOS wants is not the shape a favicon wants.** An apple-touch icon is not a smaller favicon: iOS puts the picture into an opaque container and then lays its own continuous-curvature mask over the top of it, so the picture itself is supposed to be a plain square with no transparent corners, and the rounding belongs to the platform. Our badge is a rounded square, and cutting the white ground away leaves its four corners transparent, so the file that reads as a badge in a browser tab would read on a home screen as a badge with four holes in it, showing the container's colour through the corners. The corners have to be filled in, and *how* they are filled in is the whole of the work.

Two ways of filling them were measured and both were rejected. Filling a corner with a polygon leaves a crescent of transparent pixels between the polygon's straight edge and the arc of the corner, and the alpha channel says so plainly: the extrema stay at (0, 255) rather than flattening to (255, 255). The reason is that the corner's edge is a curve — a quarter circle of radius about 210 pixels in the 930-pixel-wide artwork the badge is cropped to — and the straight chord across it is more than sixty pixels away from the curve at mid-corner, so an edge that cannot follow the curve leaves a gap that no amount of antialiasing closes. Taking instead the colour of the nearest opaque pixel for each corner pixel produces one-pixel radial stripes, and the reason is the artwork's own edge: the outermost ring of the badge is a bright blue stroke one or two pixels wide, averaging 175 of blue where the ring just inside it averages 119 and differing from it by up to 199 units in a single channel, and any fill that takes that stroke for its source drags it across the corner as a band — in that experiment 14.8% of the neighbouring pixel pairs inside the corner differed by more than 100 units, the worst pair by 178.

What fills them instead is a colour field carried outwards from the badge. The fill grows from the badge's *interior* rather than from its edge, three pixels in, because the edge is that stroke; each new pixel takes the weighted average of the pixels already filled, the four that share an edge counting for two and the four that share only a corner for one. What comes out is the badge's own field continued out to the square: not a block, not a seam, and nothing stretched — the largest jump between neighbouring pixels in a corner of the fill is 5 of 255, and the four corners are the badge's own blues, bright blue at one and near-black at the other. The badge's bright edge stroke is gone, and it has to be: a stroke cannot be squared off without being dragged somewhere it does not belong, and at 180 pixels that stroke is a fifth of a pixel wide, while on any platform that masks the icon for itself the corner that shows is the platform's anyway.

**Which pixels are the drawing and which are the export is decided by flooding, and the first version of this got it wrong.** The export's own anti-aliased rim is connected to the canvas and the drawing's near-white highlights are enclosed by the drawing, so a flood fill from the corner of the canvas separates the two where a measure of distance from white alone cannot: of the near-white pixels in the artwork the badge is cut from, 50,404 are connected to the canvas and 133 are not, and those 133 are the white reflection on the arrow. A rule that asked only how far a pixel was from white would have taken them for canvas and painted them blue, which is exactly what the first version of the fill did.

**The four artefacts are told apart by shape.** The two `.ico` files are distinguished by the sizes they pack — five frames is the favicon, ten is the shell's icon — and the two `.png` files by their dimensions, the apple-touch icon at exactly 180×180 and the wordmark at the 958×289 of a wide lockup. The size is what can tell them apart because the pixels cannot: the favicon's frames are the shell icon's own frames, frame for frame, and the wordmark and the apple-touch icon are both opaque pictures carrying the same badge. A check pointed at the wrong asset is a check that proves nothing.

What Next.js does with the file is worth recording, because "the file convention picks it up" is a claim about a mechanism and the mechanism leaves evidence. The built page carries `<link rel="apple-touch-icon" href="/apple-icon.png?apple-icon.33p4madqifatc.png" sizes="180x180" type="image/png"/>`, and the resource is served: 200, 38,678 bytes, the same bytes the committed file has. The `sizes` attribute is the second witness and it is the one iOS reads, because Next's metadata image loader writes the picture's real dimensions into it and Apple's spec names one size, 180×180 — a larger picture would be announced as a size the home screen does not ask for. Putting a 512-by-512 magenta square in the file's place and rebuilding changes the hash from `33p4madqifatc` to `0p9w9-b9k3taj` and the `sizes` from `180x180` to `512x512` at once, and a page serving the file it was built from could not have kept the same URL and declared the same size across two different files. That is the difference between a page serving this file and a page carrying a link to something.

The guard is now a guard on four artefacts rather than three. `--check` with no path checks all four and says which one it is talking about — the shell's icon, the favicon, the apple-touch icon or the wordmark — because a check that reports "something is stale" against four candidates leaves the reader to guess, and the failure this exists to catch is precisely a failure nobody can point at. The unit tests in `tests/unit/test_desktop_icon_is_current.py` went from sixteen to twenty-one, and the five new ones pin the decisions above: that the committed apple-touch icon is current with the artwork, that an icon the artwork does not generate is caught, that the check tells the apple-touch icon from the wordmark, that the icon is the 180×180 iOS asks for, and that it is a filled square rather than a badge with four holes in it.

### The pair that has to agree, and the job that now checks both of its ends

The section above ended on a sentence that was prose and not a mechanism. The
revert of the palette frame (`c631637`) said: "An artefact committed to the
repository is checked by CI on Linux and usually built on Windows, so that is
the pair that has to agree, and a change to a generated artefact is worth
checking in both before it is pushed rather than after." The tree had no way
to check in both: `make_assets.py --check` ran as a step of the `lint` job,
on `ubuntu-latest`, and nowhere else. `docs/limitations.md` §6 says the same
thing from the other side, as a limitation: "The icon artefacts are produced
on Windows and checked on Linux, and only the Linux half of that pair is
automatic."

**There is now a job for the other half.** `.github/workflows/ci.yml` carries
`assets-windows`, on `windows-latest`: a checkout, Python 3.12, `pip install
-e ".[dev]"` for Pillow, and `python scripts/make_assets.py --check` — the
same command, the same comparison (pictures against pictures) and the same
no-write contract as the `lint` job's step. It is a separate job rather than
another step in `desktop-package` because that job runs on a Windows runner
with no Python environment at all, and that is deliberate: if the NSIS
toolchain cannot be fetched, `desktop-package` "leaves the six checks below
unrun rather than green", and a second toolchain in that job would put the
six packaged-artefact checks behind a download that has nothing to do with
them.

**The run is asserted, not merely performed.** `make_assets.py --check` prints
one `the committed <artefact> is current` line for every artefact it
compared, and the job's second step requires all four of them — the shell's
icon, the favicon, the apple-touch icon and the wordmark — to have been
reported. The shape is the one `desktop-menu` uses for a skipped Electron
test, the one `desktop-package` uses for its six packaged-artefact checks,
and the one `verify` uses for its postgres-marked tests: a run that proved
nothing must not report success (ADR-0004). A check that quietly compared
three of the four artefacts, or none of them, would otherwise be a green job
that verified nothing — and the artefact it skipped is exactly the one
nobody would notice.

**What the job verifies, and what it does not.** It verifies that on Windows,
under the Pillow CI installs, the script reproduces the pictures the tree
commits. It does **not** verify that the artefacts a contributor generates on
their own machine are the ones CI checks: that still depends on the
contributor having the same Pillow, and `pyproject.toml` asks for
`pillow>=11.1` with **no ceiling**. The floor is 11.1 because Pillow changed
the PNG encoder's deflate parameters between 11.0.0 and 11.1.0, so a
developer on 11.0.0 would produce a different binary from the same pictures
— and it says nothing at all about the next major. So a Pillow release that
changes the resampling or the quantisation turns into a visible red in two
jobs at once, on a tree nobody changed: the shape of failure the palette
frame already produced, one platform further out.

**The judgement on a ceiling, recorded rather than made.** The same argument
is why `ruff` is pinned to a minor line in `pyproject.toml`
(`ruff>=0.16.10,<0.17`), and the `lint` job's comment says why: the linter
is stable across ruff minors, the *formatter* rev is not, so an unpinned
`ruff>=0.6` let the definition of "formatted" drift out from under committed
code. A **major-version ceiling on Pillow — `pillow>=11.1,<14` — is
warranted**, and the reasoning is that one: the artefacts are committed
binaries whose bytes and pixels the tree promises are derivable from the
tree, a Pillow major is exactly the kind of rev that can move them, and the
new job turns that difference into a red in two places at once rather than
one. The cost is one deliberate bump per Pillow major, and Pillow's majors
are rare (11.0 → 12.0 → 13.0 over years) while its minors have already
changed the encoder once. What a major ceiling does **not** do is protect the
bytes on its own: the encoder changed at 11.1.0, a *minor*, so `<14` keeps
the bytes reproducible only for as long as no 12.x or 13.x changes the
encoder again — a tighter bound such as `<11.2` would protect them today and
would cost every Pillow security fix in the meantime, which is a worse trade
for a dev-only dependency. The check itself compares pictures and so survives
an encoder-only change; the bound is for the *bytes* and for the pair. **It
is not made here**, because `pyproject.toml` is not this change's file.

**One document is now behind the tree.** `docs/limitations.md` §6 still reads
"The icon artefacts are produced on Windows and checked on Linux, and only the
Linux half of that pair is automatic", and that is no longer true: both
halves are automatic. The limitation was not edited here for the same reason
the ceiling was not — `docs/limitations.md` is not this change's file — but
the sentence needs to become the fact it describes, and the fact is a
narrower gap than the one it replaces: the pair is checked at both ends, and
what is *not* covered is the contributor's own Pillow, which is a
contributor's obligation rather than a hole in the tree.

### The recording the D2 walk-through will produce, and the guard that holds it to a name

§P2-D2 is the one acceptance criterion this project still owes, and the walk
that closes it is going to be recorded. The recording is therefore going to
become a binary committed to this repository, and a binary is the one kind of
change a diff does not show — an oversized file, a file under the wrong name, a
WebM wearing an `.mp4` name — so it needs the same kind of guard the committed
artwork has. This is where the convention is written down rather than left in
somebody's head.

**The convention.** The recording will be committed at
`docs/assets/d2-walkthrough.mp4`, beside `golden-path.gif` and the ten
screenshots, and the README will embed it in a `<video>` element. The format
is **MP4 (H.264)**: it is the container GitHub's README renderer plays most
reliably, every browser plays it, and its size is predictable. Playwright's
native output is WebM (VP8/VP9), so if the walk-through's capture produces
one it is kept beside it as `docs/assets/d2-walkthrough.webm` — the source the
MP4 was transcoded from — and offered inside the same `<video>` element as a
second `<source>`, for a browser that prefers it. The committed MP4 must stay
**under 10 MiB**: the file lives in this repository's history, so every clone
pays for it, and an install-and-walk recording fits in that at a modest
bitrate — a fixed 1280x720, no audio. A screen recorder's own defaults will
not do that.

**Two recordings, and two different strengths.** The `web-e2e` browser
recording and the D2 walk-through recording are not the same kind of thing, and
the README used to treat them as one. The old argument — that a screen
recording is weaker evidence than the ten committed screenshots, because a
screenshot is a state a reader can *arrive at* and check for themselves while
a video is a state someone else recorded, and because this repository's habit
is to ask the billing server what it did rather than take a run's word for it —
is strong, and it still holds, but it holds for the **browser** recording: a
capture of the dashboard moving, produced by the same Playwright run that
produces the screenshots, on the machine that built the application. It does
not hold for the walk-through's recording, whose subject is not the
application but the **install** — a stranger, on a clean Windows machine,
installing the packaged build and taking the golden path through the
operator's screens. The ten screenshots show that the app looks like this on
the machine that built it; a recording of the install shows that it installs
on a machine that has never seen it, that it starts, and that the screens are
legible to the operator using them. That is the claim §P2-D2 is open on and
the one the screenshots are not making. So the walk-through's recording is the
one to record, the browser recording is second — nearly free, a `use: { video:
"on" }` and a CI artifact, but weaker than the screenshots already committed —
and the failure states are third, because a recording of an error state
without a caption is a video of a red box, and those states are already
specified in prose and asserted in tests.

**The guard.** `tests/unit/test_d2_walkthrough_recording.py` holds the two
halves of the promise together, in both directions, because either half alone
is worthless. The README may not point at a recording the tree does not have —
the rule the README already sets for itself about the GitHub Release ("this
README will not link to a download that does not exist"), applied to a video —
and a recording committed and never pointed at is a failure too, because a
ten-megabyte file nobody can find is how a binary ends up in this
repository's history with nothing explaining it. The guard also fixes the
name, the container and the size: a file called `d2-walkthrough.mp4` has to
*be* an MP4 carrying an H.264 track, not a WebM with a new extension, and it
has to be under the ceiling. Its expectations are hand-written, for the reason
`tests/unit/test_desktop_icon_is_current.py` gives: a test that reads its
expectation from the thing it checks proves nothing.

**The line it draws between naming a file and pointing at one.** The README
has to be able to *name* the recording before the recording exists — the
convention has to be written down somewhere, or the person who runs the
walk-through does not know what to produce. So the guard treats a path in
prose, in backticks or in a fenced block as a **name**, and only a path the
README *renders* — an HTML `src`/`href`, or a Markdown link or image target —
as a **reference**. That is the same line `desktop/e2e/error-text.spec.ts`
draws between a quoted string and the prose around it, and without it the
README could not describe a file that is not in the tree yet.

**D2 has not been run, so the recording does not exist.** Nothing in the
README points at a video file, nothing under `docs/assets/` is one, and the
guard is green because both halves of the promise are unfulfilled *together*
rather than one of them.

### The promises nothing produces: a reverse drift sweep, and the eight fixes it produced

**Date:** 2026-10-21. `6ee0375` (the README's own figures), `296c89b` (the
currency an approver could be shown), `d70ff2b` (the idempotency test that was
timing-dependent), `dfb2c67` (the contracts and the data model), `ed2f325`
(the currency, refused outright), `13d21b7` (the documents' quotes), `cfa2af6`
(the error-text guard's blind spot).

The round began as a systematic sweep for the opposite kind of drift. Every
earlier pass in this repository asked whether a *document* and the *code*
described the same thing; this one asked the other way round — whether an enum
value, a status, an API type, a configuration key, a documented claim or a
translation key promised something **no code path produces**. The sweep was
exhaustive rather than sampled: every enum, every transition, every response
model, every setting and every i18n key was compared against the thing that
writes it, and its own tally was 17 real drifts, 9 deliberate ones and 5 that
need a product decision. What follows is what was fixed, in the order the
severity decides; the five left alone are named at the end, because a sweep
that does not say what it declined to change has not finished.

**① The approver could be shown a currency the money would not move in.**
`RefundArgs` — the gate-1 model for `billing.issue_refund` — declared two
parameters the billing server does not have: `currency` and
`transaction_amount`. The server's `issue_refund` takes four
(`transaction_id`, `amount`, `idempotency_key`, `reason`) and ignores anything
else, because the schema it generates carries no `additionalProperties:
false`. Nobody acted on the extra fields, and the model could see that they
existed, so it proposed them: the golden-path fixture said `"currency": "USD"`,
and so did the evaluation harness's worst-case model. That was harmless until
the one string an approver is told to trust started using it — `explain_risk`
rendered the amount in the *proposed* currency, so a proposal naming EUR
produced "This moves €129.00 EUR out of the account, refunding transaction
TX-88219, and cannot be undone automatically." for a refund the server was
about to make in the transaction's own currency, and the same proposed
currency reached the approver a second time, in the `arguments_snapshot` they
approve against.

**The currency is not knowable at that point in the run, and the honest answer
is to stop saying it.** The currency lives in the billing server's store. The
registry offers `billing.get_invoice(invoice_id)` and
`billing.list_transactions(invoice_id)` — the second needs an invoice id the
runtime does not have — and `billing.issue_refund(transaction_id)`, so before
the money moves the currency is unavailable to *any* agent, not merely to this
gate. A currency read out of the proposal is the model's guess at a fact it
cannot know. The explanation now gives the number and the transaction and says
the amount is in the currency the transaction was charged in, and
`format_amount` no longer takes a currency at all.

**The fix went in two steps, and the second is the one that was kept.**
`296c89b` first kept `currency` as a field and marked it `exclude=True`, so it
was parsed but never dumped — not into the explanation, not into the snapshot,
not into the dispatch — while gate 1's verdict on a proposal naming one was
left exactly as it was. `ed2f325` then deleted the field, which is stricter and
cheaper: a proposal naming a currency is refused at gate 1, where every other
argument the model may not supply is refused (`idempotency_key` above all),
and the model is not left proposing something that will be dropped on the
floor afterwards. `transaction_amount` was removed outright in the first commit
rather than excluded, and that one was a removal for a different reason: gate 4
compared a refund against it as a fast fail, so a model that could supply it
could also claim a larger charge and *suppress* the rule, leaving the server's
own comparison against the transaction row — the authoritative one — as the
only check. A model-supplied amount is a claim, and a claim is not a fact to
compare money against.

**And six documents still quoted the old sentence.** The explanation changed
with the currency, and six documents still carried the sentence it used to
write: `docs/api-contract.md`, `docs/case-study-duplicate-charge.md`,
`docs/data-model.md`, `docs/installer-verification.md`,
`docs/installer-verification.zh-CN.md`, and this file — where it sits inside
the M4 record of a run that really did print it, and history is left as
history. The other five now quote the application's own sentence word for word,
`docs/api-contract.md`'s two `arguments_snapshot` examples carry the fields the
snapshot carries (`transaction_id`, `amount`, and the defaulted `reason`)
instead of the currency, and the case study's proposal loses its currency for
the same reason the golden-path fixture's did: the model may not name one, so
a proposal that names one is refused at gate 1 rather than parking.

**② A guard that would have caught the third of its kind.** The same defect — a
gate-1 schema declaring a field the server has no parameter for — had
happened twice before, in `TransactionListArgs` (`account_id`, `limit`) and in
`IssueCreateArgs` (`body`), and both times no argument set satisfied both
sides. No guard could see the third, and the reasons are worth naming: the
behavioural guard dispatches the schema's **required** fields only, and a
server that ignores unknown fields cannot answer "did you accept this field?"
— it answers "did the call work?", which it did; the guard in
`tests/integration/test_mcp_contract_arguments.py` read the *document*, so a
field the schema declared and the server did not take was invisible to it; and
`test_gate_1_schema_and_server_agree_on_required_arguments` compared the
server's **required** set, so an optional one was invisible to that too. The
new guard reads the server's **own generated JSON Schema** and compares it, in
both directions, with the fields the gate actually puts on the wire — and it
refuses a field that is excluded while the server does take it, so "mark it
excluded" cannot become the way past the check. The document-reading guard was
repaired at the same time: it read one JSON object per line, and
`billing.issue_refund` is written across several lines, so the one tool whose
arguments move money was invisible to it and is covered for the first time.

**③ Two documents had the `idempotency_key` rule backwards.**
`docs/mcp-contracts.md` and `docs/tool-permissions.md` both told the reader
that an `issue_refund` call *without* an `idempotency_key` fails gate 1. It
is the other way round: the model must not supply one — a proposal carrying
one is refused at gate 1 under `extra="forbid"` — the key is derived at gate 4
from the run and the intended effect, and the runtime injects it at dispatch.
A reader following the old text would have implemented precisely the version
the design forbids, which is a model that picks its own key.

**④ A guard that failed at random, and whose barrier was decoration.**
`tests/integration/test_mcp_billing.py::test_second_writer_over_the_same_key_is_discarded`
used a `threading.Barrier(2)` with a five-second timeout to get two writers
onto one idempotency key at once. On a busy enough machine the second thread
was not scheduled inside the window, the barrier broke, the exception surfaced
inside the tool handler as `UnexpectedToolError`, the thread died, and the
test failed with a `KeyError` that says nothing about idempotency. It failed
once in four full-suite runs — it fails on the slowest machine rather than on
the wrongest one. But the barrier was not what the test was testing: the
idempotency check is a linear scan of the document *in memory*
(`mcp_servers/billing/server.py`), `_rows()` never reads the file back, and
`Store.save()` replaces the whole document with no lock
(`mcp_servers/_store.py`). Each writer scans the snapshot it loaded at
construction, neither can see the other, and the outcome is the same under any
schedule. The interleaving is now scripted rather than raced — writer A's save
is held until writer B has written — and the assertions are strictly
stronger: they name which writer survived, that the file holds one writer's
whole document rather than a row from each, and that the discarded writer's
row is not on disk. The second test is the tripwire for a future fix: it fixes
the schedule in which the second writer scans *after* the first has persisted,
which is the schedule in which the guarantee would be made (a handler that
re-reads the file, a lock, a compare-and-swap) and which the first schedule
cannot see, because there both scans happen before either write. Together the
two schedules watch both ways the guarantee could appear. The sibling test's
barrier went too — the order that matters there (the first call has returned,
so its row is in the shared document) is fixed by the code — and its join is
now a thirty-second ceiling against a hang, with the thread's exception
re-raised in the main thread so a failure names its cause. **Not one line of
the implementation moved.**

**⑤ The contracts and the data model described an application that does not
exist.** `docs/api-contract.md`'s run-detail example carried an
`idempotency_key` in `arguments` that gate 1 forbids and gate 4 derives into
a column of its own; `arguments` and `arguments_snapshot` were missing
`currency` and `reason`, which are non-`None` defaults and therefore always
present after `exclude_none=True`; `steps[].output` was `{"to": "classifying"}`
where the code writes `{"from_status", "to_status", "at"}`; and
`reasoning_summary` appears nowhere in the source at all.
`docs/data-model.md` listed eight `agent_steps.step_type` values where five
are written, fourteen `audit_events.event_type` values where seven are — one
of them, `retrieval_performed`, not merely unwritten but *misnamed*, since the
event the code writes is `retrieval_abstained` — and an `actor` vocabulary of
`agent | operator:<id> | system` where the code writes `runtime` and `worker`.
The lists are now what the code writes, with the trimmed values named as
schema reservations, so a reader querying `agent_steps` for `step_type =
'tool_call'` learns there are no rows before they go looking for the bug. The
error-code table was missing a code and naming a wrong one: the 409 row listed
three codes and `POST /api/tickets` returns a fourth, `ticket_exists`, the
only 409 that endpoint can produce; the 503 row named `not_ready`, but
`/ready` answers `{"status":"not_ready","checks":{…}}`, which is not the error
envelope and carries no code at all — and `not_ready` was in the frontend's
`ApiErrorCode` union although the API never sends it.

**⑥ Two things the user sees.** The Chinese interface showed **Approved** in
English on every decided approval: the component that renders the decision
was handed a scoped catalogue holding one section, while the labels it
renders live in another, and the error panel beside it was throwing on a
missing `common` section *inside the `catch` that was supposed to handle it* —
so a transport failure died in the handler meant to report it. The scope now
carries the three sections the component renders (`common`, `decision`,
`status`), the way the other call sites do it. And the **Expired** filter on
the approvals screen led to an empty state indistinguishable from "there are
none", because nothing in `src/` moves an approval to `expired`: the only
writer sets approved or rejected, a new approval is pending, and no TTL job
exists. The tab promised a category the system cannot produce, on the screen
where an operator decides whether money moves. It is gone, the schema-level
boundary is recorded in `docs/limitations.md`, and the two translation keys it
needed are deleted from both catalogues. If a TTL is ever implemented the tab
comes back with it — that is a product decision, and it is not made here.

**⑦ The guard that should have caught some of this had a blind spot the size
of the defect it was for.** `desktop/e2e/error-text.spec.ts` extracted three
kinds of reference — backticks, fenced blocks and double quotes — and the
approval card's wording in the English walk-through is in a **blockquote**,
which it never extracted; the corpus it compared against was three Docker
headings and two hand-written strings of the past, so `risk_explanation` was in
neither, and an explanation the application had stopped writing was not "a
message the app never shows" to it but a string with no opinion. The corpus is
now the application's own: the guard reads
`src/opspilot/domain/policies.py`, parses the string literals out of
`explain_risk`, joins the ones Python's implicit concatenation joins, and
takes the transaction id and the amount from the golden path's fixture — so a
document quoting a different sentence than the policy engine produces now
fails, and the failure names the sentence. The check stays positive on
purpose: it asks that *a* matching span exists, never that every quoted
string is one, so paths, curl lines, JSON and field names are unaffected. And
the guard had a bug of its own, found on the way: the normalisation ended with
`.replace(/[.…]+$/, "")`, a character class containing a full stop, so it ate
the sentence's final full stop as well as an ellipsis — and the explanation
ends in a full stop, so it could never match the sentence the policy engine
writes. It is now `/(\.{3,}|…)+$/`.

**⑧ A dead command, and a README quoting a run from six days ago.**
`opspilot-seed`'s `main()` raised `NotImplementedError` and the package around
it existed only to publish it; the three demo servers have always seeded
themselves from the committed JSON, so nothing was ever on that path. The
command, the package and the ruff exemption for it are removed. The README's
self-check table — the one place in this repository that promises every
figure is backed by a file or a run — was quoting a run from 2026-10-15 (13 of
13 jobs, 935 tests, 61 browser cases) for a tree that no longer existed; it
now quotes run `39439825308` at `f410911`, 16 of 16 jobs green, 994 passed and
24 skipped in each of the two Python jobs, 67 browser cases, and 998 passed
and 20 skipped locally. (The README's local figure has moved on since — the
table there is the live one, and the end of this document has the current
numbers.) The sentence under it — "every other local skip is a
`postgres`-marked test" — was already wrong independently of the numbers: of
the 20 local skips, 16 are those and 4 are platform-independent placeholders
that skip on both platforms.

**The five that were not touched, because they are not ours to decide.**
Whether `total` in a list response is the number of rows in the database or
the length of the page — it is currently the page length, which makes a
paginated list report a total that does not count the rows it did not return.
Who assembles an approval's `context`, and from which table: the field is
read out of the approval's own `arguments_snapshot` under `_context`, and no
code path writes it, so it is always empty. Whether `POST /api/tickets`
should really be atomic: the ticket insert and the run insert are two
transactions, and the compensating delete the handler offers is a `getattr`
lookup the SQL store does not answer, so a failure between the two leaves an
orphan ticket. Whether approvals should expire at all, which is the TTL
question the Expired tab was quietly asking. And the walk-through itself,
which is still owed.

## The five that were not ours to decide, decided anyway

**Date:** 2026-10-21. The section above ended with five open questions and one
of the reasons it gave for leaving them open was that they were *not ours to
decide*. Four of the five are now settled, and settled in the only direction
the codebase's own reasoning supports: not by softening the promise, but by
making the code do what the promise already said. The fifth — whether
approvals expire at all — is a product decision, and it is still open, and it
is recorded as open rather than quietly answered.

Each fix below is stated with the promise it was breaking, the code that was
breaking it, and the mutation that proves the new test would have caught the
old behaviour. The mutation is the part that matters: a test that has never
been seen to fail is not evidence.

### ① A ticket and its run are inserted in one transaction, because the contract said so

`docs/api-contract.md` §2 promised "the handler inserts the ticket and the run
in **one transaction**". It did not. The handler called `tickets.create(...)`
and then `runs.create(...)`, each of which opened and committed a session of
its own, and then — for the case where the second insert failed — asked the
ticket store for a `rollback` method:

    rollback = getattr(tickets, "rollback", None)
    if rollback is not None:
        await rollback(ticket_id)

`rollback` appears exactly twice in `src/`: that `getattr`, and the `Protocol`
declaration in `routers/tickets.py` that is what made the `getattr`
type-check. No implementation exists. The compensation never ran, so a
failure between the two inserts left a ticket that no run would ever
investigate — the exact state the contract said could not exist, and the state
the dashboard's own copy promised a user in two languages: *"a ticket can never
exist without having been investigated."*

The fix is a port, not a patch. `TicketIntake` (`src/opspilot/ports/stores.py`)
declares the one method that creates both; `SqlTicketIntake`
(`src/opspilot/adapters/persistence/repositories.py`) opens a single
`db.session_scope` and inserts the ticket and the run inside it, so the
session's one commit is the only commit there is. The compensating `getattr`
is deleted rather than fixed, because the thing it was compensating for no
longer exists.

`tests/integration/test_ticket_intake.py` proves the atomicity with a **real**
constraint violation rather than a mock: it asks the intake to create a run
whose `model_provider` is `None`, which the column's `NOT NULL` refuses, and
then asserts that **zero tickets survive**. A fake that rolled back would have
satisfied a weaker version of this test, and the weaker version is the one
that would still have passed while the SQL store was wrong.

*Mutation-verified.* Rewriting `SqlTicketIntake.create` to use two sessions —
one per insert — turns the test red with `assert 1 == 0`: the orphan ticket
reproduces on demand. Restored, green.

### ② `total` counts the rows that match the filter, not the rows on the page

Four list endpoints answered `total = len(items)`. For a paginated list that is
a number wearing the wrong label: `GET /api/runs?status=failed&limit=2`
answered `total: 2` for a filter that matched five runs, and the dashboard's
per-status tallies — the thing the API exists to serve — were counting pages.

The stores now count: `SqlTicketStore.count()`, `SqlRunStore.count(status=…)`
and `SqlApprovalStore.count(status=…)` issue a real `COUNT` against the same
filter the list applies.

**And `count` is on the port, not probed for.** The first cut of this fix
had the routers ask for it with `getattr(store, "count", None)` and fall back
to `len(items)` when the store did not have it — the same `getattr` pattern
§① describes for `rollback`, and the same mistake: a fallback that answers
with the number the contract forbids, and does so *silently*, on exactly the
stores nobody exercises. `TicketStore`, `RunStore` and `ApprovalStore` now
**require** `count`, so a store that lacks it does not satisfy the port the
routers are typed against, and the failure is a type error at the wiring
rather than a wrong number in a response. The routers have no fallback to
reach for: `total = await tickets.count()` is the only way to write one.

`tests/integration/test_list_totals.py` seeds seven rows, requests a page of
two, and asserts **both** numbers: `len(items) == 2` and `total == 7`.

*Mutation-verified* three times, because there were three places to get
wrong. Making `count()` return the page length fails with `assert 1 == 5`;
restoring the `getattr` fallback fails with `assert 2 == 7`; removing
`count` from `TicketStore` fails the port test. All restored.

**And the missing count is loud, not wrong.** A second test binds a store
that can list but cannot count — an object that does not satisfy the port —
and asserts the endpoint answers **500**, not `200` with the page length
wearing the label `total`. The test exists because the alternative failure
mode is the one this section is about: a silent wrong number, in a response
nobody can tell is wrong.

The dashboard was reading the wrong side of this and had been for a while. Its
status tallies are the reason the API has a `total` at all, and it now reads
`api.listRuns({status, limit: 1})` — one row per status, and the count from
the API. The runs page's "N records" label means *the page*, so it reads
`items.length`; before this it read `total` and therefore promised the whole
set while showing a page of it. `web/src/lib/data.ts` also carried a
`loadPendingApprovalCount` that nothing called and that the dashboard's own
headline count had replaced; it is deleted.

### ③ `decided_by` is the server's answer, not the client's claim

`ApprovalDecisionRequest` had a `decided_by` field, and the handler read
`body.decided_by or operator`. So the client named the operator: a caller
could put somebody else's name on a refund they approved themselves, and the
audit ledger would record it. That is a forged audit field on the one endpoint
where the audit field is the evidence.

The field is gone from the request schema. The handler assigns
`decided_by = operator`, where `operator` is what `require_operator` returns
after it has checked the bearer token — in Phase 1 the literal `"operator"`,
because there is one token and one role. `docs/limitations.md` §1 has been
rewritten to say exactly that, because the old text called the field
"free-text … not authenticated", which was true of the old code and false of
the new.

`test_the_decision_body_may_not_name_the_operator` asserts both halves: the
body that names an operator is refused with a 422, **and the approval is still
`PENDING`** afterwards — a refusal that had decided the approval anyway would
be a worse bug than the forgery.

### ④ The approval's `context` is assembled where the data already is

The contract (§5) promises that an approval carries a `context`: the company
and the ticket subject, so an approver can decide without opening a second
tab. The handler read it from
`approval.arguments_snapshot.get("_context")` — a key that **no code path
writes**. `context` was therefore always `null`, and the promise was always
broken, and nothing said so.

The data was never missing. When a run parks, it has already executed
`crm.get_customer` and already holds the ticket's subject.
`runtime._approval_context(ctx)` builds an `ApprovalContext` from exactly
those, the store persists it in a new `context` column, and the API returns
it. The key under `arguments_snapshot` is gone, because it was never a place
an argument belonged.

### ⑤ The operator's note is stored, and shown

`ApprovalStore.decide` had `note: str | None = None,  # noqa: ARG002 -- part of
the port signature; no column`. The API accepted a note, the port carried it,
and the store dropped it. An operator who typed "confirmed in the billing
console" was told it was accepted and it went nowhere.

There is now a `note` column (migration `0003`), the store writes it, the API
returns it in both the summary and the decision response, and the dashboard
renders it on the decided card. A remark that is stored but invisible would be
a smaller version of the same problem, so it is on the card, where the person
who wrote it can see that they did.

### ⑥ A second decision is a 409, not a 200

`SqlApprovalStore.decide` is a conditional update — `WHERE id = ? AND status =
'pending'` — and when it matched no row it **returned the already-decided
row**. The handler therefore answered 200 to the second approval of the same
refund. The contract says 409. The dashboard's decision card is built around
the 409 being the thing that stops a double-click becoming a double grant, and
it was getting a 200 with a body that looked like its own success.

`decide` now raises `ApprovalAlreadyDecided`, the router maps it to 409, and
the store's refusal leaves the first decision untouched — asserted by reading
the row back and checking that `decided_by` still names the operator who
actually decided and that `decided_at` was not rewritten.

### ⑦ `NotFoundError` is a `LookupError`, so the handler's `except LookupError` is live

The router had `except LookupError` around a store call whose "not found"
exception was `NotFoundError(DomainError)` — which is not a `LookupError`.
The branch was dead code: a missing approval fell through to a 500 rather
than to the 404 the contract specifies. `NotFoundError` now inherits from
`LookupError` as well, so the branch it was written for is the branch that
runs.

### ⑧ The committed evaluation results are guarded against the metrics drifting

`evals/results/` holds several hundred result files and **four** of them are
committed — `.gitignore` ignores the directory and those four were
force-added. They are the only source of the numbers `docs/evals.md` and the
README quote. Nothing bound them to the code that computes them: a change to
a metric's denominator, its filter or its aggregation level would silently
invalidate every published number while every test stayed green.

`tests/evals/test_committed_results_are_recomputable.py` recomputes each
committed file from today's `summarize()` and asserts that every recorded
row, every recorded breakdown entry and every independently-derived split
still agree. It reads the file list from `git ls-files`, so a result file
committed later is guarded without anyone editing the test.

*Mutation-verified.* Moving `tool_argument_validity`'s denominator by one
turns **eight** of the guard's assertions red — the pooled ratio stops
reconstructing the per-dataset split, and every recorded row stops matching.
Restored, green.

### The five, re-tallied

**Decided and implemented:** the atomicity of `POST /api/tickets`; who
assembles an approval's `context` and from which data; whether `total` is the
page or the population; whether the client may name the operator.

**Still open, and it is a product decision, not a defect:** whether approvals
expire at all. The `expired` status exists in the contract's enum and in the
dashboard's filter, and **no code path can produce it** — there is no TTL, no
sweeper, and no transition into it. The same reverse-drift sweep that found
the four above found this one and deliberately did not answer it, because the
answer depends on what the business wants: an approval that expires silently
after an hour is a different product from one that waits forever, and the
code cannot decide which of the two this is. It needs a person. The
alternative — deleting `expired` from the enum and the filter — would have
been the smaller change and the wrong one, because it would remove the
question rather than answer it.

**Also still owed:** the D2 walk-through.

### Three documents that described something the code does not do

Found by the same sweep, and fixed because a document is a promise too.

- **`docs/progress.md` pointed at a file no clone would have.** It said the
  live recording was kept at `.scratch/evidence/live-run-2026-10-15.json`.
  `.scratch/` is git-ignored; the recording is committed at
  `docs/evidence/live-run-2026-10-15.json`, and `docs/evals.md` and
  `docs/milestones.md` had it right. The pointer was simply stale.
- **The same file claimed "eight test modules" drive `duplicate_charge.json`
  and named seven of them**, one of which (`test_gate_dispatch_contract`)
  does not touch the fixture at all. The number that matters is the number of
  modules that replay the golden run: **six**, five test modules and the
  harness they share.
- **The same file's account of the live recording was wrong about the
  contract and about which gate refused.** It said the CRM contract "takes
  `customer_email`" — the contract takes `customer_email` **or**
  `customer_id`, exactly one of the two — and then quoted the **MCP
  server's** answer (`{"customer": null, "error": {… "Provide exactly one of
  customer_email or customer_id."}}`) as though the tool had produced it. The
  recorded call was refused at **gate 1**: the schema declared
  `extra="forbid"` and had no `email` field, so `validate_tool_call` raised
  `InvalidArgumentsError` and the server was never reached. The server is
  right about the contract; it is not what saw this call.

`docs/evals.md` §4 gets a paragraph too, because its sample output ends in
`raw: evals/results/2026-10-05T11-20-03.json` — a file this repository does
not contain, and not one it ever intends to. The block is a sample of the
runner's output; the paragraph now says so, and says which files *are*
committed and what guards them.

## Four more the same sweep turned up on the way

**Date:** 2026-10-21, later the same day. The five above were the sweep's
*decisions* — the promises that needed a person. These four are not decisions
at all; they are the ordinary kind of drift, found because the sweep was still
looking, and they are the reason this section exists in a document whose title
is about promises.

They share a shape, which is why they are together rather than scattered. Each
is a place where **two things were supposed to be the same and were not**:
two writers of one step type, two copies of one table, one migration chain
that had never been run against the dialect it is deployed on, and one
specification transcribed into code with nothing checking the transcription.
A drift that is a *disagreement* between two ends is invisible to any check
that looks at only one of them — which is why each of the four needed a guard
that holds the two ends to each other rather than to a literal.

### The `state_change` step had two shapes, and the contract promised one

`docs/api-contract.md` §3 says a `state_change` step "carries the transition it
accompanies: `{from_status, to_status, at}`", and adds the words **"fixed by
the writers"** — which is a promise about *every* writer of that step type,
not about the common one.

There were two writers. `SqlRunStore.set_status` — the path every live
transition goes through — writes all three keys, with `at` an ISO-8601 instant
in UTC. `TraceRecorder.record_state_change` wrote **two**: `from_status` and
`to_status`, with no `at`. A step written through the recorder therefore had
a different shape from a step written through the store, for the same
`step_type`, and a reader told "the shape is fixed by the writers" would be
told a false thing by one of them.

The recorder now writes the same three keys, in the same format. The change is
small; the reason it is in this section is that it is the third time this
sweep found the *same kind* of defect — a documented shape that one writer
honoured and another did not — and the second time the fix was to make the
minor writer match the major one rather than to relax the document. (The
first was the `note` field, §⑤ of this section.)

### Two copies in the dashboard, and the guard that now holds them to the backend

The dashboard keeps two facts that the API already knows, because the API does
not send them in a form the dashboard can use:

- **`TOOL_REGISTRY`'s permission per tool** → `web/src/lib/risk.ts`'s
  `TOOL_PERMISSION`, the fallback the approval card uses when the run detail
  — which carries the authoritative `permission` — is not loaded.
- **the error `code` vocabulary** → `web/src/lib/api/client.ts`'s
  `ApiErrorCode` union, the values a client branches on.

**Both had already drifted, and both drifts were silent.**

`TOOL_PERMISSION` was missing three of the nine registered tools —
`crm.get_account`, `crm.get_subscription` and `issues.search` — so a call to
one of them read as *"risk unknown"* where the API could have said *"read"*.
The file's own docstring claimed "every tool name at M7" is there, which was
true when it was written and stopped being true when three tools were
registered after it. The dashboard was therefore **understating** the risk of
three tools, on the one screen where an operator is asked to authorise a
write.

`ApiErrorCode` was missing four codes the API can send —
`illegal_transition`, `approval_arguments_changed`, `policy_denied` and
`unknown_tool` — all of them raised by the API's own exception handlers
(`src/opspilot/api/errors.py`) rather than by a route, which is exactly why
nobody looking at a router would find them.

Nothing caught either drift. The only test that compares the dashboard to the
backend — `tests/integration/test_web_client_types.py` — compares the
**generated** `web/src/lib/api/types.ts` against the live OpenAPI schema, and
neither of these tables is in it. The generated file covers what the API
*publishes*; these two are copies of what the API *knows*. A test that only
compares the published surface cannot see a divergence in the known surface.

`tests/unit/test_frontend_copies_match_the_backend.py` closes that, and it
closes it in **both directions**:

- the frontend's permission table has exactly the backend's tool names, with
  exactly the backend's permissions;
- the frontend's code union names every code the backend can send, and names
  **no** code the backend cannot.

The second direction matters as much as the first. A code the backend sends
and the frontend does not name arrives as a string the frontend has no branch
for; a code the frontend names and the backend never sends is a branch that
can never be taken — a claim about the API that is false, and the same kind of
false claim this section has been about all along.

**The backend's side is read by parsing, not by grepping.** There is no
canonical list of codes to import — which is the other half of the problem —
so the codes are collected from `src/opspilot/api/` with an AST walk that
recognises the three shapes they are written in: `code="…"` as a keyword,
`ApiError(status, "…")` as a positional, and `return "…"` inside
`_code_for_status`. Parsing means a code spelled with single quotes, or split
across lines, is still found, and a *variable* named `code` is not mistaken
for one.

**The frontend's side is read by parsing too, and the first parser was
wrong.** The union's members are one per line, each written `| "code"`. The
first version of the reader took the slice of the file between
`export type ApiErrorCode` and the next `;` — and the union carries a comment
*inside* its body that mentions `not_ready` in a sentence explaining why it is
not a member. The parser read that comment as a declaration and failed the
test with `the dashboard branches on codes the API never sends: ['checks',
'not_ready', 'status']`: three words from a comment, treated as three
branches that can never be taken. The reader now skips comment lines and
collects only `| "…"` members, which is what the union actually declares.

*Mutation-verified* in four directions, because a guard that has never been
seen to fail is not evidence. Adding a made-up code to the union fails the
"names no code the backend cannot" assertion; deleting `unknown_tool` fails
the "names every code" assertion; adding a made-up tool to `risk.ts` fails
the permission test's "not registered" assertion; downgrading
`billing.issue_refund` from `high_risk_write` to `safe_write` fails its
"wrong permission" assertion. All four restored, green.

### The migration chain, rendered where there is no database to render it against

`0003` — the migration that adds the approval's `note` and `context` — had
**never been executed against a PostgreSQL**, and no test could execute it here:
`tests/integration/test_migration_schema.py` drops and recreates the schema on
a real server, so it carries the `postgres` mark and skips. Docker is not
running on this machine. The chain had been applied to a SQLite file by hand
during this round, which proved the SQLite path and nothing else.

**What that left uncovered.** On a machine with no Docker, the entire
migration history — every `ALTER TABLE`, every `CREATE INDEX`, every
dialect-specific type — was unexecuted and untested. A migration that imported
a name that does not exist, rendered a type the driver has no compiler for, or
called an Alembic operation with the wrong arity would have passed every test
in the repository and failed the first time `docker compose up migrate` ran.

**Alembic's offline mode closes that without a database.** `alembic upgrade
head --sql` renders the chain to SQL text and never connects to anything, so
the whole history can be type-checked against the production dialect in a
second and a half, anywhere.

`tests/integration/test_migration_chain_renders_for_postgres.py` does that, in
a subprocess with `DATABASE_URL` pointed at a PostgreSQL URL — the environment
variable **overrides** the repository's `.env`, so the test never reads the
developer's own database and never touches it. It asserts:

1. the chain reaches head (`version_num='0003'`);
2. `0003`'s two columns render as `TEXT` and `JSONB` — the production types,
   not the SQLite ones;
3. the pgvector column renders as `vector(1536)`;
4. the HNSW index renders, with the cosine operator class;
5. the downgrade renders, and drops the two columns in the reverse of the
   order they were added in;
6. every file in `migrations/versions/` appears in the rendered chain — a
   migration that does not run is a schema the deployment never gets.

Point 3 is the one that matters most. The field failure that took down
`0001_initial_schema` was a column born as `json` and later asked to become a
vector: pgvector registers **no** cast from `json` to `vector`, so the
`ALTER` could never succeed, and the hint PostgreSQL offers (`USING
embedding::vector(1536)`) fails identically. The column has to be *created* as
a vector at the right width. Reading that off the rendered DDL is the only
way to check it without a server.

**What this does not prove, stated plainly.** It proves the chain *renders* —
every migration is importable, every operation well-formed, every type
resolving for the PostgreSQL dialect. It does not prove the SQL *executes*; a
DDL error only the server can raise is out of reach here. The two tests are
complementary, and neither replaces the other: this one runs everywhere, and
`test_migration_schema.py` runs where a server exists.

*Mutation-verified* three times: making the `context` column plain `JSON`
instead of the `JSONB` variant fails the production-types assertion; making
`note` a `VARCHAR(10)` fails it too; swapping the two `drop_column` calls in
the downgrade fails the order assertion. All three restored, green.

### The state machine is a specification, and nothing checked the transcription

`src/opspilot/domain/runs.py` says, on the table it defines:

    # From docs/agent-state-machine.md §2. This table is the specification: every
    # edge below is justified in §2.1, and COMPLETED/FAILED deliberately have empty
    # successor sets (COMPLETED -> EXECUTING is illegal and has a test).

**The document is the specification.** So `docs/agent-state-machine.md` is the
authority and `ALLOWED_TRANSITIONS` is a *transcription* of it — and nothing
checked that the transcription was faithful.

That is the same shape as the defect
`tests/integration/test_mcp_contract_names.py` exists for, and the same shape
this sweep found twice more: the MCP servers registered their tools under bare
function names and both servers' own tests passed, because the tests agreed
with the implementation and both disagreed with the contract; the dashboard's
`TOOL_PERMISSION` was missing three of nine tools and only its own docstring
noticed. Here the copy is the other way round — the document is written first,
the table is typed in, and a transcription slip would have been invisible to
every test in the repository.

`tests/unit/test_run_state.py` does not help: it derives all of its cases
from `ALLOWED_TRANSITIONS`, so the table checks itself. A table that had
drifted from the document would have passed it just as happily.

`tests/unit/test_state_machine_document_matches_the_code.py` compares the two,
in both directions, and in three parts:

1. **The states.** §1 carries the enum verbatim; `RunStatus` must have exactly
   those names with exactly those values.
2. **The edges.** §2 carries `ALLOWED_TRANSITIONS` verbatim; the code's table
   must be exactly the document's table. An edge the code has and the document
   does not is an *unjustified* transition; an edge the document has and the
   code does not is a transition that raises `IllegalTransition` when a
   workflow tries to take it.
3. **Which states are terminal.** §1's table has an "Is it terminal?" column,
   and the code's `TERMINAL` must agree with it — the one place where a
   transcription slip changes what the code *does* rather than only what it
   says.

The document's Python blocks are parsed with `ast` rather than
pattern-matched, so a reformatted block is still read and a name mentioned
outside the table cannot be mistaken for an edge. The names in §2 are bare
(`RECEIVED`, not `RunStatus.RECEIVED`) because that is how the document writes
them; they are resolved against the code's own enum, so the comparison cannot
pass on a name neither side defines.

**The document and the code agree today.** That is the point of the guard, not
a disappointment: an agreement nobody checks is an agreement that lasts until
the next edit, and this table is the one every workflow transition depends on.

*Mutation-verified* three times, in both directions: adding an edge to the
document's table fails it; adding the same edge to the code fails it too; and
changing §1's "Is it terminal?" cell for `COMPLETED` fails the third
assertion. All three restored, green.

## The approval that expires, and the one that waits forever

**Date:** 2026-10-21, later the same day. This is the fifth of the five open
questions from the sweep above — the one the sweep deliberately did not
answer, because the answer was a product decision rather than a code fact.
It has been answered now, by a person: **an approval expires.**

### What was decided, and what was not

The sweep found the gap and named it precisely: the API's `ApprovalStatus`
enum carried `expired`, the OpenAPI schema published it, the list endpoint
accepted `?status=expired`, and the dashboard's catalogue carried a label for
it — and **no code path could produce it**. The filter was a `WHERE status =
'expired'` that returned nothing, always. The dashboard had been *deliberately*
withholding the tab for exactly that reason: a tab that can only ever be empty
tells the operator "there are no expired approvals" when the truth is "no
approval can expire".

Three options were put to the person who owns that decision:

- **(a)** a TTL and a sweeper that marks the approval `expired` and leaves the
  run parked;
- **(b)** a TTL and a sweeper that *re-parks* the run with a fresh approval, so
  the work is retried;
- **(c)** remove `expired` from the enum, the filter and the catalogue, and
  keep the promise that an approval waits for a person.

**(a) was chosen.** The reasons it is the right one are worth recording, because
(b) and (c) are both defensible and a reader of the code will wonder why the
third option was not taken.

- **(c) is the smallest change and the wrong one.** It deletes the question
  rather than answering it. It also deletes a state the operator may well want
  — "this request timed out and I did not get to it" is a different fact from
  "this request is still waiting", and the inbox can only show one of them
  honestly at a time.
- **(b) is the one with a cost the codebase should not pay silently.** It means
  the same refund asks the same person again, every period, forever. That is
  right for a workflow that should not stall and wrong for one where a human
  is the bottleneck — and this system's approver *is* a human, on the slow side
  of every other step.

### What it does, and where

**`APPROVAL_TTL_SECONDS`** (`settings.py`, default 3600) is the policy: how
long a pending approval stays decidable. The default is an hour because the
approver is a human and a human is the slow part of the workflow — long
enough to act within the same sitting, short enough that a forgotten request
does not sit in the inbox over a weekend. Zero disables the sweep.

**The sweep lives in the worker**, in `worker/loop.py::sweep_expired_approvals`,
called from `poll_forever` every 60 seconds. The worker rather than the API,
for a reason the code says out loud: the worker is the process that *creates*
the approvals — it is what parks a run and asks a person — and it is the
process that is always running. A TTL enforced by a process that might not be
up would be a TTL that silently does not apply.

**The write is one conditional `UPDATE`** — `WHERE status = 'pending' AND
created_at < :before` — and the store method
(`ApprovalStore.expire_pending`, `SqlApprovalStore.expire_pending`) is the
whole of it. Two things follow from that being the only write, and both are
the point: a decision that landed a moment earlier is never overwritten, and
the sweep is idempotent.

**The run is not moved.** An expired approval leaves its run in
`waiting_approval`: nobody decided, so the money did not move and there is
nothing for the worker to execute. A run that *failed* because a person was
slow would be a lie about why it failed.

**`decided_at` and `decided_by` stay NULL.** They record who decided. Nobody
did, and a row that says a decision was taken by "an operator" at a time it
was not taken is the same kind of lie this round has been removing.

### The refusal says `expired`, and it is a different 409

`approval_already_decided` means "somebody else decided this first", and its
message names the actor and the moment — which is exactly right there. An
expired approval was decided by **nobody**, so reporting it that way would put
a person in a sentence that is false, on the one screen an operator reads to
find out what happened to their refund.

So there is a second 409: `approval_expired`, with its own message and its own
view in the dashboard's decision panel (`decision-error.ts`). The store's
refusal is a new exception, `ApprovalExpired`, and it is deliberately **not** a
`LookupError`: the route catches `LookupError` around `decide` to answer "somebody
else got there first", and had the new exception inherited that base class it
would have been swallowed by that branch and reported with a name that is
false. `ApprovalAlreadyDecided` and `ApprovalExpired` are two names for two
facts, and the route tells them apart.

### The tab comes back

`web/src/app/approvals/page.tsx` offers an "expired" filter, and the page's
own docstring says why it was missing for so long and what changed. The
filter hint is localised in both catalogues, and the count beside the panel
has its own suffix — "N expired", not "N recorded", because a timed-out
request was not recorded as a decision.

### What is still missing, and is recorded as missing

**An expired approval's run stays parked forever.** No money moved and no
decision was made, so there is nothing to execute — but there is also no
endpoint that re-proposes the refund, so the operator looking at the "expired"
tab sees a request that timed out and a run that will not move again. That is
the honest consequence of option (a), not an oversight in it, and it is
recorded in `docs/limitations.md` §7. A re-propose path is the follow-up.

### The tests

`tests/integration/test_approval_expiry.py` — eleven tests against the **real
SQL stores**, because the sweep *is* a conditional update and a fake's version
of a conditional update is a fake's answer to a question about the database.

*Mutation-verified* five times, and the third of them found a hole:

- dropping the `status = 'pending'` predicate from the sweep fails
  "the sweep does not overwrite a decision";
- inverting the cutoff fails five tests;
- making the sweep move the run fails "the sweep leaves the run parked";
- making the sweep's failure propagate fails "a failing sweep does not kill the
  worker";
- and **making `decide()` stop distinguishing an expired row passed the whole
  file** — which is how the store-level test came to exist. The route's
  pre-check catches an expired approval before `decide()` is ever called, so
  the store's branch was unreachable from the API and untested; the test now
  asserts it at the store, where the distinction is actually made.

That last one is the reason this subsection exists as well as the code: a
guard that was green because the path it covers was never taken is not a
guard. The same reasoning produced the **race** test — a store that expires
the row in the window between the route's read and its update, which is the
only way the route's own `except ApprovalExpired` branch is reachable at all.
Removing that branch from the router turns the race test red with an
`ApprovalExpired` escaping as a 500.

## Six gaps in the approval path, closed by six refusals

**Date:** 2026-10-21, later still. With the TTL in place, the approval path was
handed to an agent with six enumerated gaps and one instruction: *close them,
and prove each one closes*. It came back with four files of tests and no
production change — because every one of the six was a hole in what was
*asserted*, not in what was built. That is the good kind of finding, and it is
still six findings: a property that holds and is untested is one edit away from
not holding.

The six, and where each is now pinned:

1. **A gate-4 denial creates no approval row.** A tool the policy engine denies
   never reaches a human, so there is nothing to approve — and nothing said so.
   `test_approval_rows.py::test_a_gate_4_denial_creates_no_approval_row`.
2. **A rejected approval does not authorise execution.** `has_approved` filters
   on `status == 'approved'`, so a rejected row grants nothing — but the guard
   only covered a *pending* row. A rejected row is the one a human wrote on
   purpose, and re-entering the gates with it in place must park again, not
   execute. `test_approval_rows.py::test_a_rejected_approval_does_not_authorise_execution`.
3. **Re-parking creates no second approval row.** `_park_run` reuses the pending
   approval a call already has, because a second row would reset a human's
   in-flight decision and would trip the UNIQUE index on `tool_call_id`.
   `test_approval_rows.py::test_re_parking_does_not_create_a_second_approval_row`.
4. **The approvals router publishes exactly four routes** — list, get, approve,
   reject — and no creation endpoint. A `POST /api/approvals` would let any
   caller holding the operator token mint an "approved" row for a tool call the
   runtime never parked, and the next resume would read `has_approved(...) is
   True` and dispatch a high-risk write with no human behind it.
   `test_approval_api_surface.py`.
5. **The registry decides which tools exist; the gateway does not.** A gateway
   advertises what it *can execute*; a tool's permission must never be
   inherited from one, and an unregistered tool has no permission at all.
   `test_registry_authority.py`.
6. **The five gates run in the documented order.** Not as a comment — as the
   property that makes a refusal mean something, because a refusal says which
   gate made it. `test_gate_order.py` drives one run through four proposals,
   three of which more than one gate would have refused, and asserts which gate
   actually did.

### Every one of them was mutation-verified, and the mutations were chosen here

A test that does not go red when the code is broken is a comment with an
`assert` in it. The agent wrote the tests; the mutations that decide whether
they discriminate were chosen here, deliberately not by the agent that wrote
them. Six mutations, six reds:

| Mutation | The red |
|---|---|
| add an `approval_store.create(...)` to the gate-4 denial branch | `assert ApprovalRequest(...) is None` |
| drop `status == 'approved'` from `SqlApprovalStore.has_approved` | two tests red — `DID NOT RAISE RunParked` |
| make `_park_run` always file a fresh approval **and** drop the UNIQUE index on `tool_call_id` | `assert 2 == 1` |
| add `@router.post("/approvals")` | `Extra items in the left set: ('POST', '/api/approvals')` |
| relabel gate 2's refusal as `GATE_PERMISSION` | `assert 'gate_3_permission_lookup' == 'gate_2_registry_lookup'` |
| read `Permission.READ` at gate 3 instead of `spec.permission` | `assert 'read' == 'high_risk_write'` |

The third of those took two attempts, and the first attempt is the more
instructive one. The first mutation alone produced an `IntegrityError` — the
UNIQUE index fired before the count could be compared — so the test was red
for the wrong reason. **Red for the wrong reason is not a discrimination.** A
failure that names a constraint instead of the property has told you the
database caught something, not that the test would have caught it. Both halves
of that mutation are needed before the assertion says what it means.

### Three things it found and did not change

The agent reported these rather than fixing them, and that was the right call:
each is a decision about what the code should say, not a bug in what it does.

1. **Two of the five gate constants name stages that never refuse.**
   `GATE_PERMISSION` and `GATE_APPROVAL` are defined and exported in `__all__`,
   and the runtime writes only the other three into
   `ToolCall.rejection_reason` — correctly, because gate 3 is a *lookup* and
   gate 5 *parks* rather than refuses. But `__all__` lists all five side by
   side, which reads like a list of refusal reasons; a reader who trusts the
   export will look for `gate_3_permission_lookup` on a row and not find it.
   The fix is a comment, not a behaviour change, and it is not made here
   because the label is used by the gate-order test as a *stage name*.
2. **The relative order of gates 1 and 2 is not observable.** Gate 1 refuses
   *registered* tools with malformed arguments; gate 2 refuses *unregistered*
   tools. The two sets are disjoint, so no single proposal is refused by both
   and no refusal can witness which came first. The existing
   `test_gate_1_runs_before_gate_2` establishes the order in the only way it
   can be established here: an unregistered tool with malformed arguments is
   refused at *gate 2*, because gate 1 ran first and had no schema to fail it
   against. That shows gate 1 ran before gate 2; it cannot show that gate 1
   *would* have refused had it known the tool. What is observable among the
   refusing gates, and is asserted in `test_gate_order.py`, is gate 1 before
   gate 4, gate 2 before gate 4, and gate 3 before gate 4.
3. **A fabricated approval is currently impossible from outside the runtime.**
   The only writer of an `ApprovalRequest` is `_park_run`, inside the gate. So
   the route-set assertion in gap 4 is the *only* guard standing between "the
   API has no write endpoint" and "the API has a write endpoint" — and it is a
   guard on the *shape* of the API rather than on a behaviour, which is a
   different kind of test from the other five. It is worth keeping, and worth
   knowing that it is the only one of its kind.

### The tests

`tests/security/` — **36 tests**, six of them new across the four files above.
They run against the real gates, the real policy engine and the real SQLite
repositories; nothing is faked except the model and the tool transport, because
the controls under test are exactly the code those real components run.

## The machine finally had a PostgreSQL, and the machine was not the problem

**Date:** 2026-10-21, later still. The round began with a housekeeping task —
updating the README's own numbers — and turned into the most productive
verification this repository has had, because **Docker Desktop was installed on
this machine and simply not running**. It was started.

That one act changed what the suite could prove. Sixteen `postgres`-marked
tests had never executed here: they skip wherever no server is reachable, and
until now there was none. And the twenty-two Compose-backed browser specs had
never executed here either, for the same reason in a different guise. Both
halves of the ADR-0004 promise — "PostgreSQL is the production target, SQLite
is a tests-only path" — were being *skipped* on the machine where the code
lives.

### What running them found

**The migration chain's head was pinned to a revision that no longer existed.**
`tests/integration/test_migration_schema.py` carried `_HEAD = "0002"` while the
chain's head was `0003`. The file's own comment said "a future schema change
gets a new revision, and this test follows it there" — and it did not follow it.
The test failed on its first execution in a year and a half of existence.

**And the file had never run in CI at all.** It carries the `postgres` mark,
the `verify` job's broad step excludes `-m "not postgres"`, and no step named
it by node id. So the file whose module docstring tells the whole story of
`0001` — a migration that was green on SQLite for the whole of M0–M9 and had
**never** applied to a real PostgreSQL — was itself only ever *skipped*. Two
other files were in the same position, and each **says in its own docstring
that CI runs it**:

- `tests/integration/test_ticket_external_id_dedupe.py`: "CI's `verify` job
  names it by node id and asserts PASSED, so a skip there cannot read as
  coverage." It did not.
- `tests/integration/test_pgvector_target_is_never_destroyed.py`: "the `verify`
  job's own assertion that this test PASSED is what keeps the skip from reading
  as a pass." It did not.

So nine of the sixteen postgres-marked tests were never executed by anything.
All nine now are, and the CI step asserts them `PASSED` and fails on a skip —
the same discipline the other steps in that job already used.

**"Verify migrations reached head" verified nothing.** The step ran
`alembic current`, which prints the revision and exits 0 whether it is the head
or three steps behind it. A migration that applied only part way would have
been green there and red three steps later, in a place that reads as somebody
else's failure. It now compares `alembic current` with `alembic heads` and
fails when they differ.

**A guard of my own was making a claim it could not keep.** The file written
earlier this round said: "the revision the chain must reach is *named* rather
than read from `versions/`, so that adding a migration without updating this
file is a failure instead of a test that quietly checks the old head." That was
false. Alembic renders every revision as it walks the chain, so the old head's
stamp is still in the rendered SQL after a new revision is added — the
assertion looked for `version_num='0003'` *anywhere* in the text and would have
kept passing with a `0004` in the chain.

Proven, not argued: a temporary `0004` probe migration was added, the old
assertion was restored, and the test **passed** — the old form is blind to the
chain moving on. With the assertion reading the **last** stamp instead, the
same tree went red with `the rendered chain ends at '0004', expected the head
'0003'`. The head is now **computed** from Alembic's own script directory
(`tests/_migration_head.py`), so there is no second answer left to drift from.

### The state machine's table did not describe the machine the runtime runs

This is the one that matters, and it was found by the same unlatching: the
`postgres`-marked migration test failed, which meant reading the state machine
document for the first time in a while, which meant reading the table against
the code.

The golden path's recorded transitions, read back from the `state_change` rows
the run actually wrote:

```
received → classifying → retrieving → planning → executing
→ planning → executing → planning → executing → planning → executing
→ waiting_approval → executing → responding → completed
```

**`EXECUTING → PLANNING` is on that sequence four times** — once per tool
round, because each tool is a separate plan→gate→execute round. The table did
not carry that edge. It carried `EXECUTING → EXECUTING` instead, which the
runtime never takes. **The table described a machine the code did not run**, and
ADR-0002's central claim — "the domain owns the state machine" — was true of
the code and false of the data.

Nothing caught it because **no runtime transition was ever checked**. The
runtime assigned `ctx.run.status` directly, in four places, so a transition the
table forbade was written silently, and `IllegalTransition` — the exception
ADR-0002 exists for — was unreachable in production. The two guards that
existed were blind to it, and blind in the interesting way:
`tests/unit/test_run_state.py` compares the table against the **enum**, and
`tests/unit/test_state_machine_document_matches_the_code.py` compares the
**document** against the **table**. Both compared two artefacts that agreed
with each other, and neither with the runtime. A transcription check between a
document and its copy proves the copy is faithful — it says nothing about
whether the copy describes the thing.

What changed:

- the table carries the edge the runtime takes, and the specification's prose
  says so, including the four recorded occurrences;
- the runtime's **progression** goes through `transition_to`
  (`agents/runtime.py::_transition`), so an illegal progression raises
  `IllegalTransition` instead of being written — which is what ADR-0002's
  Decision section always said it did;
- `tests/agent/test_every_transition_is_in_the_table.py` drives the golden path
  through the real worker and asserts that **every recorded transition is an
  edge the table allows**. That is the guard the other two could not be.

The new guard was mutation-verified in both configurations, because the two
mutations fail differently and only one of them is informative. Removing the
edge from the table *with* the runtime's check in place makes the run refuse:
`IllegalTransition: illegal run transition: executing -> planning`. Removing it
*and* the runtime's check makes the guard's own assertion fire: `the runtime
took transitions the table forbids: [('executing', 'planning'), ('executing',
'planning'), ('executing', 'planning')]`. Both are red, and both name the
edge. A guard that is red for the wrong reason is not a discrimination, which
is why both were run.

**The sweep made a third writer, and the guard that exists for that had not
noticed.** `docs/api-contract.md` §3 says a `state_change` step carries
``{from_status, to_status, at}``, "fixed by the writers" — and there were two
writers when that sentence was written (the store and the recorder), so
`tests/integration/test_state_change_shape.py` compared those two. The sweep
writes a third, and a guard that compares two writers would have passed if the
third's shape were different. It compares all three now, and it took some
arranging: the sweep fails *every* run left in a mid-flight state, so it
reaches the runs the other two writers use as well, and the payload count
would not be one per writer. The sweep therefore runs first, and the runs the
other two use are created after it.

Mutation-verified: dropping the `at` key from the sweep's step turns it red
with the writer named.

**One park was deliberately left unchecked, and the reason is a product
decision.** Routing the park through `transition_to` would refuse a run that
has already reached `COMPLETED` — and refusing it *raises*, which escapes the
drive, and the worker does not swallow an exception it does not know, so a
completed run re-driven would take the poll loop down with it. The gate is a
*unit* that does not consult the run's status to decide; a completed run
re-driven parks rather than executing, and that property is pinned by a
security test. What *should* happen to a finished run that is driven again is
recorded as open in `docs/limitations.md` §7 rather than answered here.

### The ADR sweep found eleven, and one of them was this

Five ADRs were audited against the code. Eleven drifts: one high, four medium,
six low. The high one is the state machine above, and it is now corrected in
ADR-0002 as its "Correction 3" — the document's own convention for a later
finding.

The other ten are queued rather than fixed in this round, and two of them are
recorded here because they are the same *kind* of drift as the one above, and
because leaving them unsaid would be the mistake this project keeps making:

- **ADR-0004 still says the two-worker guarantee is not provided** — in three
  places, in the present tense, about a `claim_next` that the lease made false
  in commit `b9bbd5c`. ADR-0005 in the same directory already records the
  change and `docs/limitations.md` §2 has been rewritten around it; ADR-0004
  was never updated. A reader deciding whether a deployment may run two workers
  against one database would read ADR-0004 and conclude it may not.
- **ADR-0004 says the claim-predicate index "exists only in Postgres by
  design"** — and the ORM and the migration both create it on SQLite too, with
  the migration's own header saying so. The ADR's stated handling for one of
  its four named divergences is the opposite of what the code does.
- **ADR-0004 says "the only `if dialect ==` branches in the codebase are the
  four above"** — and the SQLite foreign-key pragma in `db.py` is a fifth, and
  a safety-relevant one: without it, SQLite would accept an approval pointing
  at a tool call that does not exist.

These are next round's work, and they are named here so that the next round
starts from a list rather than from a sweep.

### The browser walkthrough ran here, and reproduced its CI number

With Docker up, `npm run e2e` ran **67 passed, 0 skipped** — the twenty-two
Compose-backed specs included, and the number the README cites for CI's
`web-e2e` job. Before this, the local run was 45 passed and 22 skipped, and
the twenty-two that skipped were the ones that bring up PostgreSQL, migrate,
api and worker and click through the dashboard: the ones the M9 criterion is
about.

## A failed lookup that was recorded as a successful one

**Date:** 2026-10-21, later still. The ADR sweep had produced a list of eleven
drifts, and three more audits were dispatched against the areas it had not
covered: the MCP contract and its three servers, the desktop shell's README,
and the worker's own documentation. One of them found a defect rather than a
drift, and it is the kind that matters.

### The CRM server's refusal codes never reached the agent

`docs/mcp-contracts.md` §5.2 says it plainly: "Each server returns a
structured error object with a `code`, which the gateway maps to
`ToolCall.error` and `rejection_reason`."

The gateway maps the code by reading a **top-level** `code` in the server's
payload. The `billing` and `issues` servers put it there. The `crm` server did
not: it nested the code one level down, inside an `error` object.

So every CRM refusal — `validation_error` and `not_found` alike — arrived at
the runtime as a **successful** call. Verified end to end through the real
gateway, before the fix:

```
crm.get_customer {}                       ok=True  error=None
crm.get_customer {'customer_id':'NOPE'}   ok=True  error=None
crm.get_account  {'customer_id':'NOPE'}   ok=True  error=None
billing.get_invoice {'invoice_id':'NOPE'} ok=False error=not_found
```

The consequence is not a cosmetic difference in a JSON shape. The runtime
takes the `result.ok` branch, so the CRM call was written as
`ToolCall.status='executed'` with `error=NULL`, and the audit event was
`tool_executed` with `"ok": true, "error": null` — **a failed lookup recorded
as a success in the compliance ledger.** The agent could still read the code
out of the observation payload, so it was not blind to the failure; but the
ledger, the `tool_calls.error` column and the failure event all said the
lookup worked, and that is exactly what an audit trail exists to prevent.

**Nothing caught it, and one test actively pinned it.** The CRM server's own
tests asserted the *nested* shape (`structured_content["error"]["code"]`); the
gateway's tests asserted the top-level shape for `billing.issue_refund`; and
no test drove a CRM refusal through the gateway at all. Two suites, two
conventions, and the seam between them untested.

The worst of it was `tests/integration/test_mcp_stdio_transport.py::
test_a_structured_refusal_survives_the_wire`, whose name says the code
survives the wire and whose body asserted the opposite — `result.ok is True`,
with the message "a tool's own refusal is a successful call that reports a
refusal", and a docstring explaining that the code "lives under
`result["error"]["code"]` rather than at the top level because the server's own
result model is what carries it". The defect, described as if it were the
design.

The fix puts the code where the other two servers already put it: the CRM
result models carry a top-level `code: ErrorCode | None = None`, set on every
error path, with the `error` object kept beside it for the human-readable
message. The `issues` server's error-code literal lost its `not_found`, which
it could never produce — a query that matches nothing is an empty result, not
an error, and the generated schema is the contract the agent reasons over.

**The guard is the one that could not have existed before.**
`tests/integration/test_mcp_refusal_codes_reach_the_gateway.py` asserts two
things: that every tool of every server declares a top-level `code` in its
output schema (and that a tool declaring a structured `error` object declares
one too), and that a refusal from **each** of the three servers arrives
through the real gateway as `ok=False` with the code intact. The first is a
shape invariant that generalises to a server written later; the second is the
end-to-end property the contract promises.

Mutation-verified: removing the top-level `code` from the CRM server turns
**all three** red, each naming the defect — "these tools declare no top-level
`code` in their output schema", "these tools return a structured `error`
object but no top-level `code`", and "crm's refusal arrived as a successful
call".

### The contract's other seven drifts

The audit compared all eight tools' schemas, payloads, error codes and names
against the contract. The names and the permissions were clean — all eight
agree across the contract, the servers and `TOOL_REGISTRY`. The rest:

- **The CRM output is nested and the contract showed it flat.** The contract
  used the same notation for `billing.get_invoice`, which *is* flat, so only
  the CRM blocks were out of step — and the code has already voted for the
  nested shape: the runtime reads `record.result.get("customer")` and says so
  in a comment. The contract now draws the wrapper.
- **Six of the eight tools had no documented failure mode at all.** Each now
  has one line, and the two that *cannot* fail say so.
- **`billing.list_transactions`' rows were missing `refund_id`** — the one
  field that says which of two apparently identical charges has already been
  given back, and so the detection surface depends on it.
- **`issues.search`'s example showed an issue that does not exist.** The
  `issues` server's seed holds **no issues at all**, so a fresh store answers
  `{"issues": [], "total": 0}`; the example now shows `OPS-1001`, which is the
  first key the store allocates, and says what a fresh database answers.
- **`issues.create`'s output was missing `description` and `labels`.**
- **`issues.search` declared a `not_found` it could never produce** — fixed in
  the server, not only in the document.
- **§5.2 claimed the code reaches `rejection_reason`.** It does not, and
  should not: `rejection_reason` names the *gate* that refused a call, while
  a tool that ran and failed writes its code to `ToolCall.error`. The
  sentence now says so.

### The desktop shell's README described a window it does not have

Five drifts, two of them medium. The one that matters is a section headed
*"Why this window is English even when the dashboard is not"*, which argued
that "there is no mechanism by which this window could follow the dashboard's
locale, only a mechanism by which it could be *wrong*".

There is such a mechanism, and the shell uses it. The shell loads the dashboard
**in its own window**, so the dashboard writes `opspilot_locale` into Electron's
own session cookie store; `src/menu.ts` reads it out of that store
(`readLocaleCookie`) and rebuilds the native menus when it changes
(`watchLocaleCookie`); and `src/menu-policy.ts` holds the copy for both
locales and pins the cookie name to the dashboard's. The README's reason was
wrong because it described the *browser hand-off* — a mechanism this package
does not use.

The project's own `docs/limitations.md` §7 already said the opposite: "The
native menu is localized; the operating system around it is not." So the
README was the outlier, and it is now corrected: the **menu bar** follows the
cookie, the **bootstrap page** cannot (it renders before the cookie exists),
and the section says which is which.

The second medium drift: the README said the ownership decision "is made
once". It is made **twice** — first "is the API already serving `/ready`?",
then, only if it is, "does that backend accept this app's operator token?" A
backend running under a different token is **refused**, not adopted, and the
window says so. A reader deciding whether to run the app beside an existing
stack was told the wrong thing.

The three low ones: five Electron calls, not six (in the README *and* in
`security-policy.ts`, which repeated the number); `npm run package` is four
steps, not three; and the CORS row now says that the shell always exports the
variable, so Compose's `:-http://localhost:3000` default never applies to a
stack this app started.

### The remaining ADRs, corrected

The ADR audit's eleven drifts: the high one was the state machine, and it is
fixed. The other ten are now corrected too — ADR-0004's three statements about
the two-worker guarantee (the lease closed that, in commit `b9bbd5c`, and
ADR-0004 was still saying in the present tense that it was open), its row on
the claim-predicate index (the partial indexes are emitted on **both**
dialects, as the ORM and the migration both say), its completeness claim about
dialect branches (the SQLite foreign-key pragma is a fifth, and a
safety-relevant one), ADR-0005's worker entry point and its lease-log
sentence, ADR-0003's quoted literal (it holds five fields, not one), and
ADR-0001's "one file" (the MCP SDK is imported in two, both under
`adapters/tools/`).

### The postgres-marked tests are order-independent now

Running the whole marked selection in one go — `pytest -m postgres` against a
disposable database, which is the command the guard's own error message tells
a developer to run — failed, and the failures were not in the code under test.
The ticket-dedupe test inserted a row and left it there, and the guard
(`tests/_pgvector_target.py`) refuses a database that holds a row, so every
postgres-marked test that ran after it failed with a refusal that read like a
defect.

The fixture that owns the schema now empties it on the way out — `TRUNCATE`,
not `DROP`, because the tables have to survive: the guard admits a database
whose tables exist and hold zero rows, and its CI-shape test asserts that they
do. **All sixteen now pass in one run, in one order.**

## A terminal run that still reported a holder, and a gate that promised a fast fail it did not make

**Date:** 2026-10-21, later still. The two remaining audits came back — the
worker's documented behaviour, and `tool-permissions.md` + `milestones.md` —
and between them they found two defects and about thirty drifts. The defects
are worth the whole round.

### A `FAILED` run still reported a lease holder

`docs/api-contract.md` §3 states an invariant: *"`claimed_by` and
`claimed_until` are `null` **together** … A run that reaches a terminal state
therefore never reports a holder, even if a worker held it a moment earlier."*

There are two ways a run becomes `FAILED`, and only one of them released the
lease. `SqlRunStore.set_status` clears both lease columns on a terminal
transition. The worker's **boot sweep** did not go through `set_status` — it
issued its own bulk `UPDATE`, which set `status` and `failure_reason` and
nothing else.

So on the **ordinary worker-crash path** — worker A claims a run and dies
mid-drive; worker B boots and sweeps it — the run became `failed` while still
carrying A's lease. The API projects the lease with no status check, so the
run-detail page showed `status=failed`, `claim_lease.state=held`, and a
`claimed_by` naming a worker that no longer existed. The contract said a
terminal run never reports a holder, and the dashboard renders that field
precisely so an operator can tell a held run from a run nothing claimed.

The sweep now does what `set_status` does. It reads and writes the rows one at
a time rather than in one statement, because the thing it has to reproduce is
not one write but **three**: the lease release, `completed_at` (which
`docs/agent-state-machine.md` §4 says is set on entry to `COMPLETED` or
`FAILED`), and the `state_change` step (which the same section says every
transition writes — a swept run had none, so its trace showed a run that
simply stopped). The rows are read under `FOR UPDATE SKIP LOCKED` on
Postgres, so two workers booting at once take disjoint rows and the
`UNIQUE (run_id, sequence)` constraint cannot be tripped by two writers
allocating the same sequence number.

**This is the second time in one session that a bulk `UPDATE` was the wrong
tool.** `mark_interrupted_runs` bypassed `set_status` to be one cheap
statement, and in doing so it skipped three things that statement does. A
shortcut that duplicates a statement's *effect* has to duplicate all of it.

`tests/integration/test_interrupted_run_sweep.py` pins the three properties
and the states the sweep spares. Mutation-verified three ways, each naming its
own property: dropping the lease release gives `assert 'worker-A' is None`,
dropping `completed_at` gives `assert None is not None`, and dropping the
step gives `expected exactly one transition into failed`.

### Gate 4 promised a fast fail the policy engine does not make

`docs/tool-permissions.md` §3's gate-4 comment said that "cannot refund an
already-refunded transaction" is "enforced *here* as a fast fail and *again* at
the MCP server as the authoritative check".

`evaluate_policy` implements exactly four rules: the denylist, the idempotency
replay short-circuit, the refund ceiling, and the refund-exceeds-transaction
rule. There is **no state precondition**, and there cannot be one — the policy
engine is a pure function over the proposal's arguments and the run, and the
transaction's status is a row only the billing server can read.

So the consequence is real: an already-refunded transaction is **not** refused
before the approval gate. The run parks, a `risk_explanation` is generated for
a refund that cannot happen, a human is asked to approve it, and only then
does the dispatch come back `invalid_state`. The tree's own
`docs/agent-state-machine.md` §3.1 records the refusal as server-side — the
permissions document described a two-layer defence the code does not have.

The comment now names the four rules that exist and says plainly that the
server's `invalid_state` is the only already-refunded refusal. Building the
missing layer would mean a new read and a new trust boundary for a rule whose
outcome the server already enforces authoritatively.

### The rest of the drifts

**`docs/tool-permissions.md`**, five more: §3.2 said "two strings reach the
approver", and there are now three kinds of content on that card — the
model's `reason`, the deterministic `risk_explanation`, and the approval's
`context`, whose `ticket_subject` is **customer-supplied text** and fits
neither category; §3.2 claimed the argument snapshot is enforced "by a test
that attempts to alter them between approval and execution", and no such test
exists — the exception fires from a *status* guard, and the real mechanism is
that `set_status` has no `arguments` parameter at all; §6.1 said the 409 comes
from "two independent checks" and there are three refusal paths now, with
`approval_expired` among them and the whole TTL lifecycle undocumented; §5's
ledger has **seven** rows rather than six, and the currency row's adversary is
not in `tests/security/` at all; and the header named
`src/opspilot/domain/permissions.py`, a module that does not exist.

**`docs/milestones.md`**, seventeen: M7 described a defect as "reported here
rather than fixed" that the *same commit* had fixed — the client does read
`API_INTERNAL_BASE_URL`, and `web/e2e/web-service.spec.ts` is the regression
guard for it — and fifteen stale figures, refreshed against the tree:
71 modules (not 73), 211 formatted files (not 209), 31 `Settings` fields (not
29), 46 MCP tests (not 43), six page lengths, five `approval-card.tsx` line
references, and seven `file:line` citations.

**Elsewhere**: `docs/architecture.md` said `agents/runtime.py` owns
`RunStatus` and `ALLOWED_TRANSITIONS` — the domain does, and ADR-0002 has said
so since it was written — and its state diagram left out the re-plan edge the
runtime takes four times; `docs/data-model.md` named a concurrency test that
does not exist; `docs/limitations.md` quoted a `worker-compose` worker id that
is no longer any default; ADR-0005's SQL block showed the bare claim without
the lease clause that actually prevents the duplicate; and the worker's own
module docstring listed `EXECUTING` among the states the boot sweep fails,
which was the one place left in the repository that still said so.

### And a guard for the drift class that has now bitten twice

A documentation citation of the form `` `Symbol:line` `` is a claim about
where something lives, and it was checked by hand — at the moment it was
written, and never again. `docs/milestones.md` put `RunStatus` at line 30,
`ALLOWED_TRANSITIONS` at line 51 and `AgentRun.transition_to()` at line 113,
for as long as the
file existed; the definitions are at 41, 62 and 192. The project has been
bitten by this class before — commit `e906f8d` is "docs: five drifted figures
and eight line citations that pointed elsewhere" — and that fix was manual
too, which is a repair rather than a fix.

`tests/unit/test_doc_symbol_citations_point_at_the_definition.py` resolves
every such citation to its definition in the source tree and asserts the line
matches. A symbol with **no** definition is a failure — that is a symbol that
was renamed or deleted, and a citation of it is a claim about nothing — while
a `host:port` citation (`localhost:8000`) and a `path:line` citation
(`metrics.py:432`) are counted as unchecked rather than failed, and the count
is printed so a green run is not read as full coverage. A second test
asserts the guard still *sees* the two citations that rotted, so it cannot
quietly become a no-op.

Mutation-verified both ways: moving one line off the citation gives "points
at RunStatus being defined at 41", and renaming the symbol in the source
without updating the document gives "names RunStatus, which is not defined
anywhere in the source".

## Four audits, two of them found promises the code never made

**Date:** 2026-10-21, later still. Four more audits came back — the eval
harness's documentation, the Phase 2 acceptance table with the installer
procedure, the desktop shell's Playwright specs, and the risk register with the
case study. Between them they found 35 drifts, and five of them are the same
shape: **a document stating something stronger than the code does**, in a place
where a reader would act on it.

### The Chinese dashboard that said "Proposed" in English

The dashboard localises four contract enums through `web/src/i18n/enums.ts`. A
status with no label does not break the page: `labelFor` catches the missing
key and falls back to `humanise(value)`.

**The degradation is the problem.** `humanise("proposed")` is "Proposed", which
is right in English and *wrong on a Chinese page* — one English word where the
rest of the screen is Chinese, on the one screen the operator is deciding on.

And the catalogue was missing it. It had `status.toolCall.pending`, a status
the backend does not have, where the backend's `proposed` was missing. The
*extra* key is what hid the hole: a reader counting keys saw five entries and
called the group complete, while the value the API actually sends fell through
to the English fallback.

The catalogue now carries `status.toolCall.proposed` ("Proposed" / "已提出"),
and no longer carries the two phantoms (`status.toolCall.pending`, and
`status.approval.awaiting_approval`, which is not an approval status at all).

**The guard is the one that could not have existed before.**
`tests/unit/test_frontend_copies_match_the_backend.py` already held the
dashboard's *permission table* and its *error-code union* to the backend, in
both directions. It now also holds the **status labels**, in both directions
and in both locales. Mutation-verified: putting `pending` back sends it red
with the extra and the missing both named.

### A risk level, and a rule the docstring did not implement

`web/src/lib/risk.ts` derives the approval card's risk level from two sources
— the tool call's `permission` field, and a transcription of the tool registry
used as a fallback — and returns a *provenance* alongside it.

Its module docstring said: "the *higher* of two answers wins". **The code never
did that**, and the argument turns out to be against the field for a reason the
paragraph missed: the approver must see **the permission the call was gated
under**, and that is exactly what the tool call row carries — the runtime
snapshots the registry's permission into the row at the moment the gate read
it. The table, by contrast, is the registry *as it is today*.

There is a second reason the old rule could not have been implemented as
written: the card states where the level came from, and the provenance
vocabulary has exactly three entries. A level taken from "the higher of two"
has no honest provenance sentence.

`web/e2e/risk-derivation.spec.ts` now pins the rule, and the docstring says
what the code does. Mutation-verified by swapping the two sources' order.

### The eval harness promised a stronger guarantee than the code makes

**"The live recording supplied the first non-empty instance in any committed
result … Until then the bucket had no evidence behind it."** It did not. The two
committed 2026-10-13 runs already carry **24 and 21** non-empty schema-refused
proposals — the metric recomputes them — and the live recording supplied the
first *non-eval* instance of the same shape. The same false generalisation was
in `metrics.py`'s own docstring, so the document had inherited it rather than
invented it.

**"'The system refused' is checked as 'no `HIGH_RISK_WRITE` ToolCall reached
`status='executed'`'."** It is not, and the difference is the whole point. The
metric counts a violation only for a high-risk execution with **no** approval
request behind it — and the runner **approves parked runs on purpose**, so the
approved call executes, because a safety case that parks is the golden-path
pause. An approved high-risk write *does* reach `status='executed'`, and it is
not a violation: it is the approval gate working.

A reader would have taken the gate's `0` as "no high-risk write ever executed",
when it means "no high-risk write executed *without a human deciding to*".

Also corrected: the `safe-011` example is `safe-010` in the dataset; the
recording's final response has `escalated: false`, so the escalation was in the
model's prose and not in the field the metric reads; the recorded rejection
reason is `gate_1_schema_validation`, a gate name, not the contract's
`validation_error` result code, because the tool never ran; and the SDK note's
claim that the transport's required `idempotency_key` is "one of the five
gates satisfied by the transport rather than by a hand-written check" — the
transport is not one of the five gates, and the check that matters at gate 1
is hand-written.

### The risk register claimed a guard that does not exist, and the case study called an unforgeable column forgeable

`docs/risks.md` R1's residual said the orchestration adapter is "Guarded by a
test asserting the adapter does not import from `domain.policies` or
`domain.permissions`". **No such test exists**, and `domain/permissions.py` is
not a module — the permission table lives in `domain/tools.py`. The only
layering guard in the tree is `tests/unit/test_layering.py`, which asserts that
`domain/`, `ports/` and `agents/` import no third-party root and no adapter; it
never inspects the orchestration adapter, which is excluded from coverage and
imported by no test. The residual is real and unguarded, and the register now
says so.

`docs/case-study-duplicate-charge.md` §5 said "`decided_by` is free text and
is *not authenticated*", and cited `docs/limitations.md` §1 for it. §1 says the
opposite — "The client cannot forge it" — and the code agrees with §1:
`ApprovalDecisionRequest` has **no** `decided_by` field and is
`extra="forbid"`, so a client that tries to name itself is refused rather than
believed. The claim was wrong, and wrong in the direction that makes the
system look *less* careful than it is — which is a reason to fix it, not a
reason to leave it.

Also corrected: C1's resolution cited a `risk_explanation` string no code
writes; C2 described the pre-resume mechanism (Phase 1 *preserves and resumes*
an interrupted run rather than failing it); C4 promised a citation-subset check
no metric performs; and the case study's two retrieval anchors named headings
that do not exist — the real retriever over the committed corpus returns
`#when-this-procedure-applies` and `#ineligible-reasons`, and running it here
confirmed both.

### The Phase 2 table, the installer procedure, and the desktop specs

The acceptance table: **no row labelled "met" turned out not to be met**, and
every command in the installer procedure still resolves. The two medium drifts
were the i18n leaf counts (334/330, frozen at the commit the plan cites; they
are 338/334 now) and the procedure's description of the dashboard — "a
left-hand nav (Tickets, Runs, Knowledge, Approvals) and an empty ticket list"
— where the nav is a **bar across the top** with five items starting with
"Dashboard", and the landing page shows a *Recent runs* panel reading "No runs
yet".

The desktop specs: the shell resolves the **operator token before it probes
Docker**, so on a machine with no configured token the window names the token
and the spec — which asserts "Docker is not installed" — fails for a reason it
is not about, without ever stating the precondition. The launch now supplies a
disposable token, so the test sets up the state it is about rather than
inheriting the machine's configuration. And the renderer's sandbox check, which
was a `test.skip` inside a helper, made Playwright report **the whole test** as
skipped on every platform without it — so the assertions that *had* run
reported as skipped too. It is its own test now.

**And the `lifecycle` job was downloading Electron.** The `desktop-check` job is
named "Lifecycle tests (no Electron, no Docker, no browser)" and the project's
own config says the `lifecycle` project is "Pure functions, fakes, no Electron
and no Docker". But `security.spec.ts` imported the navigation guard from
`src/security.ts`, and that module imports `electron` for its *values* — and
`require("electron")` **downloads the Electron binary at import time** when it
is not already installed (the package's own `index.js` does it, and throws if
the download fails). So the cheapest job in CI was fetching ~100 MB of
Electron during a test run, and the spec's own docstring — "The policy module is
free of `require("electron")` for exactly this reason" — was true of the policy
module and not of the module the spec also imported.

The guard needs only Electron's `WebContents` **type**, and `import type` is
erased at compile time. It now lives in `src/security-web-contents.ts`, which
has no runtime dependency on Electron at all, and the spec imports it from
there. Proved rather than argued: with the Electron binary's `path.txt` moved
away — the state a CI runner is in — the whole `lifecycle` suite passes in 1.6
seconds and **no download is attempted** (`path.txt` is not recreated), and the
compiled `dist/security-web-contents.js` contains no `require("electron")` at
all.

## Three more audits, and a contract that named a code the API never sends

**Date:** 2026-10-21, later still. Three audits came back: the API contract
against the API, the dashboard's browser specs against the dashboard, and the
helper scripts against everything the repository says about them. Between them:
27 drifts, and four of them were a document promising something the code does
not do.

### The OpenAPI document named a code the API never sends

`docs/api-contract.md`'s preamble says the generated OpenAPI document "is
treated as part of the contract, not as documentation that drifts". The
document's description for a **422** named `validation_error`. The API sends
`schema_invalid` for every 422 it raises — the one 422 path in the codebase is
FastAPI's request-body validation.

A client generated from the document would have typed a branch on a code the API
never sends, and would have had no typed branch on the one it does. The module
that builds the document states the rule it broke: *"The descriptions name the
*code* a client branches on, never the message."*

The 422 now names `schema_invalid`, a **500** is declared (the contract's §6
lists one for every endpoint and the document declared none — an undeclared
500 is not a client that handles it, it is a client whose type says the failure
cannot happen), and `/ready` declares its **503** (whose body is the readiness
shape and deliberately *not* the error envelope).

**The guard is the one that could not have existed before.**
`tests/unit/test_the_error_vocabulary_agrees_with_the_api.py` (first committed as
`tests/unit/test_openapi_error_descriptions_name_the_codes_the_api_sends.py`,
renamed when the check grew to cover the API side too)
cross-checks the generated document against the contract's §6 table — the
*written* contract, not a copy of it in the test, so a third answer cannot
drift. Every code a description names must be one the table maps to that same
status. Mutation-verified: putting `validation_error` back on the 422 turns it
red with the mismatch named.

### The operator's remark was stored and invisible

`ApprovalSummary` declares a `note` field and the store persists the operator's
own remark — but the summary projection never read it, so `GET /api/approvals`
and `GET /api/approvals/{id}` answered `note: null` for an approval whose
remark was sitting in the database. A remark an operator wrote was invisible on
the page they wrote it from.

It is projected now, and the contract's examples carry it. (The decision
response always did — the dashboard's card shows the note after deciding.)

### The duplicate `external_id` is a 409, and the spec said it was a 500

`web/e2e/failure-states.spec.ts` asserted, in the same paragraph, that the API
answers `500 internal_error` for a duplicate `external_id` and that the operator
therefore sees "a generic message for a completely foreseeable operator
mistake". Both halves stopped being true when the API learned to catch the
violation and answer `409 ticket_exists` — and the *form's own comment*
repeated the same stale claim, so a reader of the form would have reached the
same wrong conclusion.

Both are corrected. The spec's exclusion list also used to present itself as the
file's complete list of gaps, and the dashboard has since gained a **third**
operator-facing failure state — the **expired** approval, on the money path,
with its own copy that nothing pinned. It is pinned now, in a file of its own
(`web/e2e/expired-approval.spec.ts`): a browser case stages the expiry with
the worker's own TTL sweep — the same conditional `UPDATE` the worker runs,
with the run's filter added so it cannot touch another run's approval — and
asserts that the panel says *"This approval expired — this click granted
nothing."* and **not** "Already decided", because "already decided" would put a
person in a sentence that is false when nobody was there at all.

**Two things the browser work taught, both worth writing down.**

*The fake model provider drives exactly one golden run per scenario.* In
scenario mode it answers the recorded calls **in order**, with a cursor per
method — so a second ticket submitted against the same scenario finds the
cursor past the end and the run never gets anywhere
(`UnmatchedFixtureError: no recorded generate_structured response matched
scenario 'duplicate_charge'`). The case therefore cannot have a run of its own
inside a file that already has one, and it lives in its own file with its own
stack. That is a constraint of the harness, and it was learned the hard way.

*One failing test takes the next one down with it.* Playwright restarts the
worker after a failing test, `beforeAll` runs again against a brand-new stack,
and the state the later test was reading is gone. The first version of the
expired case failed for a reason that had nothing to do with what it was about
(it waited for a park that another case had already consumed), and the case
*after* it then failed too, on an empty list. A test that reaches for a state
another test has consumed is a test that fails for a reason of its own, and one
that fails that way costs its neighbours as well.

### A test that could never fire, and a recipe that could not run

`web/e2e/i18n.spec.ts` had a second half that watched the **browser's**
requests for port 8000 and compared the set it saw in each locale. But the
dashboard's reads are made by the Next.js process, not by the browser — the
browser only calls the dashboard's own `/api/...` Route Handlers, on the
dashboard's port. So the set was always empty and the assertion compared `[]`
with `[]`. It would have passed with `?locale=zh-CN` on every API URL, which
is the exact failure its own comment said it was there to catch.

What is observable is the API's *answer*, and the answer is on the page: a
refused call is an error panel, not an inbox. The assertion now says **no
load-failure panel in either locale** — which is stronger than comparing URL
sets, because it asserts the API *accepted* the calls.

**And the README's headline "reproduce the GIF" command could not run.** It
says `node scripts/record_demo_gif.mjs`, and the script does
`import { chromium } from "playwright"` — from `scripts/`, which has no
`node_modules` of its own and no root `package.json` to inherit one from. The
only Playwright is in `web/node_modules/`, which Node's resolution never
reaches from `scripts/`. So the recipe a reviewer is told to follow died at
module load and regenerated nothing, while `docs/risks.md` told them the
artefact was "regenerable".

The script now resolves Playwright from `web/` explicitly, and the hard-coded
`C:/Users/admin/.../chromium-1234/chrome.exe` is gone — one user's home
directory, naming a browser build that is not even the installed one.

`scripts/openapi_schema.py` had the same kind of slip: it set
`OPSPILOT_DATABASE_URL` to redirect the settings away from a developer's
database, and `OPSPILOT_DATABASE_URL` is not a name the settings layer reads
— the field's alias is `DATABASE_URL`. The redirection never happened, so the
"belt-and-braces" the docstring relied on was absent, and a future eager engine
would have used the developer's own database. It sets the right name now, and
the two helpers that claimed a consumer they did not have are gone.

## A gate that cannot refuse, and a citation that points at the wrong function

**Date:** 2026-10-21, late. The last two audits — `tool-permissions.md` and
`milestones.md` — came back with 17 drifts between them, and the first of them
was a gate that is described as refusing something it cannot refuse.

### Gate 4 is credited with a rule it cannot fire

`docs/tool-permissions.md` §3's gate-4 comment listed "exactly four" rules:
the denylist, the idempotency replay short-circuit, the refund ceiling, and
the refund-exceeds-transaction rule.

**The fourth cannot fire.** `_refund_exceeds_transaction` needs the charged
amount, and `RefundArgs` deliberately does not declare it — a model that could
supply the number could also *suppress* the rule by claiming a larger charge.
`domain/policies.py` says so itself, and a unit test pins it. The check is
enforced, at the **billing server**, under a different code
(`amount_exceeds_transaction`).

So this was not a hole but a misattribution — and the kind that could make a
future maintainer delete the server's check on the strength of §3. The comment
now says gate 4 has **three** live rules and names where the fourth lives. (The
"exactly four" wording was mine, earlier in this round: a rule that cannot fire
is not one of the rules, and writing the count down made the claim checkable.)

The same audit found §3's **gate 1** described as a gate that cannot refuse —
"invalid arguments never reach here; if they do, this is a programming error and
raises" — while the code records a malformed proposal as a **rejected
`ToolCall`**. The document contradicted its own §3.2 and §4, which both say
gate 1 *does* refuse. It does now, in the pseudocode.

And two rows of §5's adversary table pointed at files that do not assert what
the row claims: the "reach `COMPLETED` while a gate was skipped" row is pinned
by `test_prompt_injection.py::test_the_run_never_reaches_completed_while_parked`,
not by the file the row named; and the "make retrieved instructions change
gate 3's permission" row names a file whose own comment says the control under
test is gate **5**. The rows say what their adversaries actually assert now.

### A ticked acceptance criterion that was not met

`docs/milestones.md` ticked: *"`tests/security/` has an adversarial test per row
of `tool-permissions.md §5`'s right-hand column"*, with the parenthetical "six
rows, six adversaries in `test_approval_bypass.py`, whose docstring maps each
row to its test name".

The column has **seven** rows. The currency row's adversary is deliberately
**not** in `tests/security/` — a currency never reaches the approver, it is
refused at gate 1 — so the criterion is met for six of seven. And
`test_approval_bypass.py`'s docstring claimed to implement that column
"verbatim" while listing a different six: two items the column does not have,
and three it does.

The criterion says six of seven and names the currency row's real home. The
docstring says what it actually implements. (A ticked box is a claim about the
tree, and a reader auditing the adversarial ledger from it would have drawn a
wrong conclusion about the coverage.)

### Seventeen more, and a guard for the class

The rest were line numbers that had moved — `repositories.py:395-420` cited for
`SqlRunStore.set_status`, which is `TicketStore.count`'s docstring;
`tracing/recorder.py:179` cited for a method defined at 180; `runner.py:1201-1219`
cited for a function defined at 1220 — plus two counts that had moved (the i18n
catalogues are 337 and 333 string leaves, not 338 and 334, because a phantom
key was removed this round) and one that was never right (18 of the 31 `Settings`
fields ship a value, not 16).

**The class has bitten this repository three times** — commit `e906f8d` is
"docs: five drifted figures and eight line citations that pointed elsewhere",
and this audit found eight more. Every one was fixed by hand, and a manual fix
to a defect that recurs is a repair rather than a fix.

`tests/unit/test_doc_line_citations_point_at_the_code.py` now checks the two
citation shapes that carry a claim about specific code:

1. **A named symbol beside a citation** — the symbol must be defined in the
   cited file, and the cited lines must be **inside** its block.
2. **A quotation beside a citation** — the quoted text must appear in the
   cited lines.

It found **five** real drifts immediately — the four above plus a `db.py:150`
that pointed at a closing parenthesis rather than the `CLAIMABLE - {RECEIVED,
EXECUTING}` expression one line up. Mutation-verified: moving one citation to
a line that does not mention the symbol turns it red, with the line's content
in the message.

The guard covers 19 of the 144 `path:line` citations in the documents; the
other 125 are **bare** — a path and a number with nothing beside them — and
there is nothing mechanical to check them against, so they are counted and
printed rather than checked. The coverage is visible rather than implied, which
is the difference between a guard and a claim.

## The cross-references that pointed at nothing

**Date:** 2026-10-21, late. While five audits swept the code, this one swept
the **documents' own cross-references** — the `§` references and the markdown
links that hold the documents together.

### What was broken

One reference, in three places: `docs/progress.md` cited
`` `docs/mcp-contracts.md` `` with a section id of the form ``S2`` — and **that
document has no such section**. Its
second section is `` ## 2. `billing` server ``. The "S" is a leftover from a
numbering the contract no longer uses, so a reader following the reference
finds nothing — three times, in the middle of an argument about an argument
set that gate 1 and the server disagreed on.

(This paragraph names the bad id as ``S2`` rather than as a section reference,
because the guard below checks *every* ``§`` reference in these documents, and
a paragraph *about* a broken reference must not itself be one.)

Everything else resolved: 105 link targets, 5 anchors, and 464 of the 467
section references. The other three are prose — "§" used as "see" — and the
guard tells them apart by shape rather than by guessing.

### The guard

`tests/unit/test_doc_cross_references_resolve.py` checks three things:

1. every markdown link's file target exists;
2. every markdown link's anchor is a heading in the file it points at, compared
   against the headings' slugs the way GitHub writes them;
3. every `§` reference **that looks like a section id** names a section some
   document offers.

The third needed one rule worth writing down: **`§` is used in these documents
both as a section reference and as a prose "see"** ("the detail is in § The
170 calls below"). A guard that checked the prose would be guessing. The
discriminator is the *shape* of a section id in this repository — every one
contains a digit or a hyphen (`2`, `3.1`, `M4`, `P2-D1`) — and prose does
not. So a reference is checked when it carries a digit or a hyphen, and
counted-but-not-checked when it does not.

A document offers a section key when one of its headings carries one, and a
**row** of a table whose first cell is an identifier offers that identifier
too. A section's prefix with a row's identifier makes the composite: the
Phase 2 plan's section "P2-A" and its acceptance row "A1" give "§P2-A1", which
is how the milestones ledger cites the plan — and getting that right is what
took the unresolved count from 55 to 6.

Mutation-verified: pointing one reference back at a section id of that form
turns it red, naming the document and the key.

### What it does not do

It does not check that a reference points at the **right** section. That is a
question of meaning, and a guard that guessed would misfire — so it checks the
half that is mechanical: that the thing referenced exists. The coverage is
printed rather than implied: 105 link targets, 5 anchors, 464 section
references, 3 read as prose.

### What the tree looks like after it

`ruff check` and `ruff format --check` clean. Bare `mypy` clean over 233
files. `pytest` — **1162 passed, 21 skipped** (1183 collected); the 21 skips are
16 `postgres`-marked tests, 3 M0-skeleton placeholders and 2 eval-fixture skips
for committed result files written before the breakdown was recorded — and all
sixteen of the first are executed by CI's `verify` job: seven of them named by
node id and asserted `PASSED`, the other nine by their file's exit code plus an
assertion that nothing in it skipped, so a skip there cannot read as coverage.
On the web side, `tsc --noEmit` and `eslint` clean, `npm run check:types`
reports that the generated client types are current with the live OpenAPI
schema — which is the check that would have caught `decided_by` being removed
from the request schema and `note` being added to the two response schemas
without the dashboard's client being regenerated — and `npm run e2e` is **85
passed, 0 skipped** with Docker running.


### The tree this entry describes has never been through CI

**Date:** 2026-10-22. The line above says what the tree looks like after the
work; this entry says what has *not* happened to it, which is the other half
of the same sentence.

The last CI run on this repository is `39473344611`, and its head commit is
`ba23531` — the same commit `origin/main` is on. Everything above this entry
(the audit-driven fixes, the guards they added, the documents they corrected)
lives in commits **on top of** that one, and has therefore never been executed
by GitHub Actions. The README's figures table quotes run `39439825308` at
commit `f410911`, which is further back still.

**What that means for the claims in this document.** "CI green on every push"
is true of the pushes that were pushed, and the tree described above is not one
of them. The numbers in the block above are local runs, and the CI assertions
they rely on — the node-id `PASSED` checks, the six packaged-artefact checks,
the four asset checks — have never executed on GitHub since they were written.
The honest way to read this entry is: **the work is verified locally, and the
first CI run on it has not happened yet.**

Three things follow, and all three are worth saying plainly:

- **Nothing here is exempt from CI's own gates.** The suite runs the same
  `pytest`, the same `ruff`, the same `mypy`, and the same guard files the
  workflow runs; what has not happened is GitHub running them, not the gates
  being absent.
- **The audit that found this** read the workflow and the log, and the two
  disagreed in the way that matters: a `desktop-menu` step claimed to check
  that the Electron binary's shared libraries were all present, and under
  `pipefail` its `&&` branch was unreachable -- so the step printed "every
  shared library the Electron binary opens is present" and exited 0 **in exactly
  the case where one was missing**. The step reads `grep`'s verdict now, and
  the `eval-smoke` step's error message is reachable again (a command
  substitution that fails aborts the assignment that holds it under `bash -e`,
  so the step was still red and the diagnosis was lost).
- **The figures in this block were recomputed against this tree**, not carried
  over: the earlier ones said 1090 passed over 210 mypy files and 76 browser
  specs, and the tree says otherwise.

Two claims that were repeated in two documents have been corrected to the
truth, because a number that can be checked should be: the sixteen
`postgres`-marked tests are all executed by the `verify` job, but **seven** of
them are named by node id and asserted `PASSED`; the other nine are held by
their file's exit code and an assertion that nothing in the file skipped. The
stronger claim -- "each named by node id and asserted `PASSED`" -- was in the
README and here, and it was wrong by a factor of two.

---

## Seven audits, one of them found a promise nobody kept

**Date:** 2026-10-22, later still. Seven sub-agents swept the tree for
*reverse drift* — a document, a docstring, a test name or a comment claiming
something the code does not do — and every one of them died part-way through
with an HTTP 400 from the provider. Their partial work was finished by hand,
verified line by line, and the parts that were wrong were corrected before
landing. One of them had cited a test file that did not exist.

The finding that mattered most was in the **risk register**. `docs/risks.md`
R3 argued that the eval demonstrates the approval gate can be *refused*:

> The eval's `safety` set includes cases where the correct human answer is
> Reject.

It does not. Every row of `evals/datasets/safety.jsonl` expects a `completed`
run — there is no refused terminal anywhere in the corpus — and the harness
approves **every** parked run, which `src/opspilot/evals/runner.py::run_case`
says in its own docstring: *"The approval is unconditional, and that is a
deliberate limitation of the harness, not of the system."* So the eval
measures what the model *proposes* and what the gates *do*; it cannot measure
whether the human's judgement is right, because the human in the harness always
says yes.

That claim was false **in the direction that flatters the system**: it was
being used as evidence for a property the corpus does not have. The README's
scenario 2 does exercise a rejection, and that half was always true.

### What the other six found

| Where | The claim | The truth |
|---|---|---|
| `adapters/tools/composite_gateway.py` | the internal gateway "is asked first", the MCP half second | the registry's `server` picks exactly one half; there is no fallback |
| `adapters/tools/mcp_gateway.py` | `AttributeError` and friends "are left to propagate" | every `Exception` subclass reaches the wide catch, and the comment said why it was wide |
| `adapters/tools/mcp_gateway.py` | the same mistake gets one code | it gets `validation_error` in process and `tool_error` over stdio |
| `adapters/tools/mcp_stdio.py` | a queued call "is failed rather than abandoned" | the queue is FIFO, so the sentinel lands *behind* the call and the call is answered |
| `adapters/tools/knowledge_tool.py` | a `functools.partial` of `retrieve` satisfies the retriever shape | `retrieve` takes `top_k` keyword-only, so a partial cannot be called that way at all |
| `tracing/recorder.py` | the recorder writes the state-change step | the run store does, in the same transaction as the status update |
| `agents/prompts.py` | this is the prompt a run sends | the runtime carries its own; this is the *default* the provider adapters fall back to |
| `agents/runtime.py` | — | the two prompt copies had nothing holding them together |
| `api/app.py` | the health routes are token-free because they are registered first | they are token-free because the health router declares no auth dependency |
| `api/routers/runs.py` | the output keys are asserted by `test_run_detail_against_the_real_store.py` | that file asserts the *store*; the file that asserts the keys is `test_trace_labels_are_read_from_real_steps.py` |
| `domain/approvals.py` | the approval and the run's transition are one transaction | two writes, two transactions; a crash between them leaves a pending approval behind a run still in `EXECUTING` — the safe direction |
| `domain/tools.py` | `requires_idempotency_key` is schema-enforced at gate 1 | gate 1 does the opposite: a proposal that *carries* a key is refused |
| `domain/policies.py` | the engine checks state preconditions | it does not; "already refunded" is the MCP server's `invalid_state`, and only the server's |
| `domain/errors.py` | `UnknownTool` is a 422 | no route raises it; gate 2 records the name and the run carries on |
| `migrations/env.py` | the online path "does not force a single connection" | it opens one and hands it to Alembic |
| `migrations/versions/0001` | "the test suite asserts this predicate matches `CLAIMABLE`" | nothing compared the migration's literal with the domain's |
| `tests/unit/test_schemas.py` | a refund without an idempotency key "fails validation" | it *validates*; the stub was skipped from M4 to M7 and its name said the opposite |
| `docs/architecture.md` | the deployment is `apps/api`, `apps/worker`, `apps/web` | there is no `apps/` directory |
| `docs/api-contract.md` | `unknown_tool` is a 422; `not_ready` is a 503; `mcp_unavailable` is a refusal code | none of the three is what the contract said |
| `docs/mcp-contracts.md` | cited as a section id the document does not have, from four places in the source | section 2; the documents had been cleaned and the source had not |

### The guards

Seven new guard files, each of which was **mutation-verified** — the property
broken on purpose, the guard confirmed red *for the right reason*, the property
restored:

| Guard | What it holds |
|---|---|
| `tests/evals/test_the_docs_about_the_eval_corpus_agree_with_it.py` | the documents' claims about the eval corpus match the corpus |
| `tests/unit/test_the_docs_cited_test_names_exist.py` | a cited `test_*` name resolves, or its paragraph marks it as retired |
| `tests/unit/test_the_readme_walkthrough_figures_match_the_specs.py` | the README's browser-walkthrough counts are the tree's |
| `tests/unit/test_gateway_claims_match_the_code.py` | the tool adapters' own promises |
| `tests/unit/test_source_docstrings_cite_a_test_that_exercises_them.py` | a docstring's evidence citation names a file that exercises the symbol |
| `tests/unit/test_the_runtime_and_the_prompt_module_agree.py` | the two prompt assemblies do not drift apart |
| `tests/unit/test_migration_claims_match_the_domain.py` | the migration's claim predicate is the domain's `CLAIMABLE` |
| `tests/unit/test_the_dashboard_routes_are_not_placeholders.py` | the six dashboard routes reach the API and nothing imports the placeholder |

Two existing guards were extended rather than added to: the cross-reference
guard now reads the **source trees** as well as the documents (that broken
reference survived in four source files after the documents had been cleaned),
and it accepts a section *range* -- sections 3 to 4 -- when both endpoints
resolve.

### What the numbers are now

The figures in the README were recomputed against this tree rather than carried
over. The counts that were wrong before this round — "twenty-two
Compose-backed specs" (the tree has twenty-three), "the CI run's 67 plus nine
specs added since" (it is eighteen, and the table under it said so while the
prose said nine) — are now checked by a guard rather than by a reader paying
attention.

---

## The project wrote 2,172 directories to the system drive, and left them there

**Date:** 2026-10-28. A request to move everything this project had written to
the C: drive into the repository, and to stop writing there at all, turned
into the largest single cleanup this repository has had, and the finding that
made it worth doing was not a tidiness one.

**The inventory.** On this machine, the project's files on C: were:

| What | Where | How many | Size |
|---|---|---|---|
| per-eval-case scratch directories | `%LOCALAPPDATA%\Temp\opspilot-eval-*` | 1,494 | 197 MB |
| the desktop packaging specs' scratch | `%LOCALAPPDATA%\Temp\opspilot-packaging-*` | 140 | |
| the Electron menu specs' profiles | `%LOCALAPPDATA%\Temp\opspilot-menu-*` | 41 | |
| pytest's base temporary directory | `%LOCALAPPDATA%\Temp\pytest-of-admin` | 7 | 225 MB |
| electron-builder's installer cache | `%LOCALAPPDATA%\opspilot-desktop-updater` | 1 | 117 MB |
| Electron's user data | `%APPDATA%\OpsPilot` | 1 | 15 MB |

**All of them are gone from C: now.** The first four were the system's temp
directory, which the environment had already emptied; the last two were moved
into the repository and then deleted, because they were the previous runs'
residue and nothing in them was worth keeping. C: holds none of this
project's files now, and it will not start holding them again -- that is what
the rule below is for.

**Two defects, not one.** The first is the **leak**: the eval harness made a
private directory per case and threw the handle away, so every case of every
run left a directory behind. The second is the **location**: even the
directories that were cleaned up went to the system temp directory, which is
outside the repository, where `.gitignore` cannot protect them and `git status`
cannot show them. A temporary file under the repository is one the developer
can see and delete; a temporary file under `%LOCALAPPDATA%\Temp` is invisible,
and the only way to find out how much disk it has taken is to go looking.

**The rule now.** Everything this project writes at run time goes under the
repository's own gitignored `.opspilot/` directory:

- `src/opspilot/paths.py` is the single decision: `scratch_dir()` for
  disposable files, `data_dir()` for the MCP servers' live stores, both
  overridable with `OPSPILOT_SCRATCH_DIR` / `OPSPILOT_DATA_DIR`. The
  repository root is found by walking up to the `pyproject.toml`, **not** from
  the current working directory -- the MCP servers' data directory used to be
  `<cwd>/.opspilot-data`, so the same code wrote into the checkout when run
  from the checkout and onto the system drive when run from a terminal
  elsewhere.
- The root `conftest.py` points `TMPDIR`/`TEMP`/`TMP` at the repository and
  clears `tempfile`'s cache, so pytest's base temporary directory follows.
- The eval runner, the eval CLI, the demo script, the assets builder, the
  OpenAPI schema dumper, the type generator, the GIF recorder and the four
  desktop e2e specs all write inside the repository.
- The desktop app's Electron user data -- the GPU cache, the locale cookie,
  the operator token -- was `%APPDATA%\OpsPilot`. A development run puts it in
  `.opspilot/electron-userdata`; a packaged app has no checkout to write into,
  so it keeps the default unless `OPSPILOT_USER_DATA_DIR` says otherwise.

**The guard** is `tests/unit/test_the_project_writes_only_inside_the_repository.py`:
every call that chooses a temporary location must name a directory, and the
one exception -- the store's temporary file, written beside the file it
replaces, which is what makes the replacement atomic -- is checked rather than
trusted. Mutation-verified: removing the `dir=` from the harness's per-case
directory turns it red and names the line.

**What is still on C:, and why.** The Claude Code session store
(`%USERPROFILE%\.claude\projects\G--OpsPilot`) and its task-output directory
are the **tool's** files, not the repository's: they hold this conversation's
transcript, and moving them out from under the running app would empty the
session list. They are the one thing on C: that this repository cannot
redirect, and they are reported rather than moved.

---

## A cleanup that broke a guard, and what the guard now says

**Date:** 2026-11-08. The C: cleanup moved the project's scratch directory
from the system temp directory into the repository, and that broke a test
three files away.

**What broke.** `desktop/e2e/packaging.spec.ts` has a test called "still
refuses a development launch started outside any repository". It asserts that
the stack-root lookup throws when the working directory is outside a checkout.
It used to `chdir` into the system temp directory -- which is outside -- and
the cleanup moved the scratch directory to `G:\OpsPilot\.opspilot\tmp`, which
is two levels below a `docker-compose.yml`. The lookup found it, and the test
failed with "Received function did not throw". The test was right before the
move and wrong after it.

**Why nothing noticed.** The Python suite is green; the desktop e2e is a
Playwright project that needs a built dashboard, and the change to it was a
one-word substitution in a path. **Changing where a test's working directory
comes from is a change to the test's premise, and the premise is what the
assertion is about.** The test now finds a directory that is outside *every*
repository -- the roots of the drives the machine has -- and checks each
candidate rather than assuming it: a drive root that happened to hold a
`docker-compose.yml` would make the test pass for the wrong reason, which is
the failure this file exists to catch.

**The generalisable rule.** A change to *where* something is written is a
change to what the tests around it mean, and the tests that read that location
have to be run, not reasoned about. The cleanup's own guard
(`test_the_project_writes_only_inside_the_repository.py`) is green, and it
was green while this test was red -- because it checks a different thing.
A guard that passes is evidence about the property it holds, and about
nothing else.

**Also found in the same sweep, and held now:** the operator-facing messages
the verification documents quote verbatim were checked for their *titles*
only -- the detail and the remedy were copied by both documents and compared
by neither -- and the claim that "the operator token is still absent from
every browser bundle" had no test behind it at all. Both now have one, and
the corpus comes from the application itself rather than from a
transcription of it.

---

## The provider every test runs on claimed a provenance it does not have

**Date:** 2026-11-08. Four agents swept the tree for reverse drift -- the
documents, the desktop app, the web component layer, and the test suite's own
claims -- and the finding that mattered most was in the file the whole suite
stands on.

**`src/opspilot/adapters/models/fake.py` is the default provider.** It is what
a fresh clone runs, what CI runs, and what the deterministic demo runs, so its
docstring is read by everyone who wants to know what the green means. It said:

> **It replays, it does not invent.** Every response comes from a fixture
> captured from a real provider interaction.

The fixtures said otherwise. Both committed ones carry ``"recorded": false``
and a note reading "HAND-WRITTEN PLACEHOLDER, not a recording", and neither
carries a ``request_hash`` -- so the request-hash replay path the same
docstring describes has nothing committed to match against. The scenario path
serves them: the calls come back in order. The docstring also contradicted
itself ten lines below, where the fixture format it shows is labelled
``"recorded": false,  # false == hand-written placeholder``.

``docs/risks.md`` R2 has said so since the risk register was corrected; the
provider's own docstring had not caught up, and the provider is the file a
reader reaches for first.

**The four agents' findings, in one place.**

| Where | The claim | The truth |
|---|---|---|
| ``readiness.py`` | a branched migration directory "returns the first" head | alembic raises; the catch turns it into ``unknown`` |
| ``ports/stores.py`` | ``TicketIntake.create`` names no failure | the implementation raises ``TicketExists``, through the store it calls |
| the ports guard | -- | it could only see exceptions raised **in the method itself**, so it could not see an exception raised by the method that method calls |
| ``evals/README.md`` | the tree holds ``runner.py``, ``metrics.py``, ``test_harness.py`` | none of the three is in ``evals/`` |
| ``docs/limitations.md`` | the local embedder "reaches Recall@5 = 14/20" | it reaches **13/20** over the corpus as it stands, and the score bands moved with it |
| ``docs/phase2-plan.md`` | compose's three ports are at ``:68`` / ``:261`` / ``:515`` | they are at **:73**, **:274** and **:528** |
| ``docs/milestones.md`` | the browser still reads from the API directly | the dashboard's README now says every read is made from the server |
| ``desktop/src/menu-policy.ts`` | the menu is Electron's "item for item" | the same file says two entries are not carried over |
| ``desktop/e2e/token.spec.ts`` | "the token is absent from every browser bundle" | nothing checked it |
| ``web/src/i18n/dictionaries/zh-CN.json`` | -- | one value began with a space, and the JSX beside it prints the separator too, so the banner showed two spaces |
| ``pyproject.toml`` | ruff excludes ``apps/web`` | there is no ``apps/`` directory |
| ``src/opspilot/settings.py`` | answerable cases score ``>= 0.2500`` | the measured minimum is **0.2608** |

**Two of the findings were mine.** The C: cleanup moved the scratch directory
into the repository, and ``desktop/e2e/packaging.spec.ts`` has a test that
``chdir``s into it to be "outside any repository" -- so that test went red,
and the Python suite stayed green, because the Python suite does not run the
desktop e2e. Changing *where* something is written changes what the tests
around it mean. It is recorded in the entry above.

The other was mine in a different way: the first version of the new fixture
guard's pattern matched a single space where the docstring wraps its line, so
the guard sat green against exactly the sentence it was written for. The
mutation found it. **A guard that has never been seen to go red is not a
guard.**

---

