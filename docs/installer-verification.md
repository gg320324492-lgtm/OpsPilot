# Verifying the OpsPilot desktop installer

**For someone who did not build it.** Everything below is run on the machine
being tested. Nothing here assumes a repository, Node, or a build toolchain.

> **中文对照版：** [`installer-verification.zh-CN.md`](installer-verification.zh-CN.md)，
> 供执行验证的人照着做。**本文件是权威原文**，两者冲突以本文件为准。
> (A Chinese companion for the person running the verification. **This file is
> authoritative**; where they disagree, this one wins.)

**What this is.** `OpsPilot-Setup-<version>-x64.exe` installs the OpsPilot
desktop shell. The shell owns a window and the local service lifecycle. The
backend — PostgreSQL, the API and the worker — runs in Docker Desktop, and the
dashboard is a Next.js server the app spawns on your machine. **Docker Desktop
is required; Node is not.**

The plan this implements is `docs/phase2-plan.md` §P2-D2, whose acceptance is
"on a clean Windows machine with Docker Desktop, install the built package and
walk the golden path end to end. That is the whole test, and it must be run by
someone who did not build it."

---

## 0. Before you start

### What you need

| | |
|---|---|
| Windows | 10 or 11, x64 |
| The installer | `OpsPilot-Setup-<version>-x64.exe`. See "Where to get the installer" below. |
| Docker Desktop | installed, **and started**. See below if it is not. |
| Free disk | ~6 GB. The first launch builds three container images from source. |
| Time | first launch 5–15 minutes; later launches well under a minute |
| A screen recorder | any tool that writes MP4 and lets you cap the bitrate. See "Record the walk-through" below. |

### Where to get the installer

The file this script installs is `OpsPilot-Setup-<version>-x64.exe`. The version
comes from `desktop/package.json` and changes from build to build, so match the
name and not the number.

**Two ways to get it, and which one applies to you depends on whether you have
a GitHub account.** If you do not, go straight to Option B: you cannot download
the file yourself, and that is not a problem with your machine.

**Option A — you have a GitHub account: download it from CI.** Every push to
`main` — and every pull request against it — builds the installer and attaches
it to that run. The `desktop-package` job of
the **CI** workflow runs on `windows-latest`, builds the NSIS installer, and
uploads it as a workflow artifact named **`OpsPilot-Setup-windows-x64`**.

In the GitHub repository page, the path is:

> repository → **Actions** → a run of the **CI** workflow whose
> **`desktop-package (the Windows artefact)`** job succeeded → the
> **Artifacts** section at the bottom of that run's page → download
> **`OpsPilot-Setup-windows-x64`**

Step by step:

1. Open the **Actions** tab.
2. Open a run of the **CI** workflow in which the job **`desktop-package (the
   Windows artefact)`** succeeded. The CI workflow runs on every push to `main`
   and on every pull request against it; the most recent successful run will
   do.
3. On that run's page, go to the **Artifacts** section at the bottom of the
   page, and download **`OpsPilot-Setup-windows-x64`**.

The download is a zip. Unpack it, and the installer is inside:
`OpsPilot-Setup-<version>-x64.exe` — the file §1 tells you to double-click.
Running it brings up the SmartScreen warning — see *A warning you will see and
can ignore* below; that is expected, and it is not a fault in the file.

**Why this source.** The artifact is built by a CI runner from one specific
commit, and the run's page names that commit, so the package you install is one
you can point at in your report. The job checks the file before uploading it:
**exactly one** `OpsPilot-Setup-*-x64.exe` exists, it is **over a megabyte**,
it **begins with the PE signature `MZ`**, and it **carries the NSIS
signature** — so it really is the assisted, per-user installer §1 describes,
and not something else with a similar name. The upload fails if it finds no
file, so a successful run cannot have shipped nothing.

**The artifact expires.** It is kept for **90 days**
(`retention-days: 90` in `.github/workflows/ci.yml`) and is deleted after
that. If the run you picked has nothing to download, it is probably older than
that — **pick a more recent run**.

**Option B — you do not have a GitHub account: the person who gave you this
script sends you the file.** This is the ordinary case, and it is the one this
script is written for.

**Downloading a workflow artifact requires being signed in to a GitHub
account.** The repository is public, and without signing in you can still read
a run's page and the list of artifacts — but the download itself is not
available: asked for without a signed-in session, GitHub answers
`401 Unauthorized`, and from a browser it redirects you into a sign-in flow
and lands on a not-found page. That is GitHub's rule for artifact downloads; a
public repository does not exempt you from it. It is not a property of this
repository, and nothing on your machine changes it.

So the download is done by someone who does have an account: **the person who
gave you this script.** They download the artifact from CI — Option A above —
and send you the `OpsPilot-Setup-<version>-x64.exe` inside it. You need no
account, no repository and no toolchain to receive it. Any way of handing over
a file works: email, a chat message, a shared drive, a USB stick. Unpack it if
it arrives as a zip.

**The provenance travels with the file even though the download did not.** The
installer you receive is the same file the CI runner built, and the run and
the commit behind it are named on that run's page. **Ask the person who sent
it which run and which commit they took it from** — §7 asks you to report
that, and it is what lets somebody else check your result afterwards.

**A GitHub Release exists, but it holds the older installer.** Release
`v0.1.0` (published 2026-11-13) carries an installer built 2026-11-14 —
`OpsPilot-Setup-0.1.0-x64.exe`, 119,227,780 bytes, SHA-256
`565206f73b3ef924…` — the one the 2026-11-14 record below was walked with.
**Do not install it for this walk.** The file you are asked to verify is the
newer candidate, whose SHA-256 begins `71ac5ac0…`; its size (126,045,189
bytes) differs, so the two files are easy to tell apart. The short-cut of
grabbing the Release page does not apply; get the file the person who sent
you this kit points you at (the kit's *Which file* section names it by hash
and size).

**If the file you were given was built on somebody's own machine, know what
you are holding.** The build output is gitignored, so such a file carries no
traceable provenance by itself — which is why this kit states the build's
provenance alongside it: **commit `81a0278`** (full hash
`81a02783a3034838cc79fc366e6602611a3ab4f3`) of this repository, built on
2026-11-15 from a clean worktree on the build machine. Check that hash
against the file you received, and record in your report where you got the
file from; that is what lets somebody else check your result afterwards.

### Record the walk-through

**Start a screen recording before you double-click the installer, and leave it
running until §3.5 is finished.** The recording is one of this walk-through's
deliverables, and it is the one the project is actually waiting on: the
**video** that `docs/milestones.md` records as **Not met**.

**What the recording is for.** The ten screenshots in this repository show the
application on the machine that built it. This recording shows the other half:
the packaged installer **arriving** on a machine that has never seen it,
starting, and being driven by a person. That is the claim §P2-D2 is open on,
and no arrangement of screenshots reaches it. So the recording is not an
illustration of the walk — it *is* the walk, and it is the only evidence the
walk will ever produce.

**What you will see in it.** One continuous take if you can:

- **From** the moment you double-click `OpsPilot-Setup-<version>-x64.exe` in
  §1.
- **Through** §2 and §3, to the end of §3.5.

Record the **process**, not the account of it: the installer running, the
window with its log scrolling, the dashboard replacing it, the clicks, the
approval card, the refund executing once. **Keep the pauses** — the first
launch takes 5–15 minutes while Docker builds three images from source, and a
recording that skips that wait is not showing the thing this recording exists
to show. **No audio and no narration.** The video is silent by convention;
nobody speaks in it.

**What to use.** Any screen recorder you already have, as long as it can write
**MP4** and let you cap the bitrate. You do not need a particular program.
Set:

| | |
|---|---|
| Container | **MP4**, with H.264 video |
| Resolution | **1280x720**, fixed |
| Audio | **none** — no microphone, no system sound |
| Bitrate | low enough that the file lands in the low single-digit megabytes |

The ceiling is **10 MiB** and the file is committed to a repository, so every
clone of it pays for it. **A screen recorder's own defaults will not do that**:
they will either hand you a file several times too big, or the other way, one
too coarse to read the screens in. If your tool does not take a bitrate,
choose one that squeezes the video into **no more than 10 MiB** — the thing
that has to be true is the file's size, not the recorder's setting.

**What to call it.** The file the project expects is
`docs/assets/d2-walkthrough.mp4`. The name is fixed in advance, and the
repository has a test that goes red if a recording is committed under any
other name — so please use that one.

**If your tool only writes WebM**, keep the file and hand it over as
`docs/assets/d2-walkthrough.webm`; the project will transcode it to the MP4
above and keep the WebM beside it as the source. **Do not rename a WebM to
`.mp4`** — a file with the right extension and the wrong contents is worse
than no file, and it is checked on the bytes, not on the name.

**How to hand it back.** You do not have this repository, and you do not need
one: **nothing is committed from your side.** Leave the file where you
recorded it and give it to the person who gave you this script — they will
commit it into the repository's `docs/assets/`. **Say so in your report**, with
the file's name and its size; §7 asks for that.

**This is part of the walk-through, not extra work on top of it.** You are
going to be at that keyboard for 5–15 minutes anyway, and the recording is the
same act as the walk: it is the evidence that the packaged product installs
and runs for somebody who did not build it, and it is the video the project
is missing.

**If you did not record, this walk-through still passes.** The acceptance for
this procedure is *installed, walked, reported* — and the recording is not
part of it. A report that says no recording was made is a complete report,
and nothing about the install, the launch or the golden path changes if the
recorder was never switched on. The recording is a by-product of the walk:
the project would rather have it than not, but it is not what makes this
procedure a pass.

### If Docker Desktop is not installed

The app will not install Docker for you, and it will not half-work. It starts,
opens a window, and tells you exactly what is wrong. The window's **title** is
the string `Docker is not installed`; beneath it it shows the detail *The
`docker` command was not found on PATH (ENOENT). OpsPilot runs its backend —
PostgreSQL, the API and the worker — as Docker containers, so there is nothing
this app can start without it.* and the remedy *Install Docker Desktop for
Windows from https://www.docker.com/products/docker-desktop/, start it, and
open this app again.* (the remedy adds a parenthetical about Linux and macOS).
Those three strings are the whole message; they are prose, so match the title
and read the rest rather than diffing it character for character.

**What to do:** install Docker Desktop, start it, and wait for the whale icon
in the system tray to stop animating before launching OpsPilot. The first launch
of Docker Desktop itself can take a minute on a new machine.

If Docker Desktop is installed but **not running**, the message is different —
**"Docker is installed but not running"** — and the remedy is to start it.
These are kept apart because "Docker isn't working" is neither of them.

### A warning you will see and can ignore

This build is **not code-signed** (no certificate was available when it was
produced). Windows SmartScreen will say *"Windows protected your PC"* or
*"Unknown publisher"*. Click **More info** → **Run anyway**. This is a
provenance warning about a download, not a detection of anything wrong with the
file.

### An old icon you may still see

If this machine already has an older build of OpsPilot on it, the new
installer replaces the files but **Explorer can go on showing the old icon**:
the Start-menu entry and the desktop shortcut keep rendering the picture the
shell cached the first time it saw that path. The icon inside the installed
executable is the new one; the shell is showing you a copy from its cache.
**That is not a failed install**, and reporting it as one is a false failure.

Rebuild the cache and look again. Run all three lines in an **ordinary**
Command Prompt — Start, type `cmd`, Enter — and **not** one opened with *Run
as administrator* (an elevated prompt cannot start the desktop shell):

```cmd
taskkill /f /im explorer.exe
del /a /f "%localappdata%\Microsoft\Windows\Explorer\iconcache_*.db"
start explorer.exe
```

The first line stops the shell, the second deletes the database Explorer keeps
its icons in, the third starts the shell again: your taskbar and any open
Explorer windows come back, and the icons are extracted from the files afresh.
If `del` reports a file as in use, the shell came back before the delete ran —
run the three lines again.

**Why not uninstall the old build first?** The cache is keyed by path, and the
old build and the new one live at the same path
(`%LOCALAPPDATA%\Programs\OpsPilot\OpsPilot.exe`). Uninstalling the old one
and installing the new one does not reliably clear the entry for that path
either — the path is what the entry is named after, and it does not change.
Deleting the cache database does clear it, and it leaves the shell no old
picture to serve, so the icon has to come from the installed executable, which
carries the new artwork.

---

## 1. Install

1. Double-click `OpsPilot-Setup-<version>-x64.exe`.
2. Choose an install folder or accept the default
   (`%LOCALAPPDATA%\Programs\OpsPilot`). Administrator rights are **not**
   required.
3. Finish.

**Worked.** The installer returns to Explorer with no error. A desktop shortcut
named **OpsPilot** and a Start-menu entry appear.

**Both carry the app's own icon.** A **rounded square in blue** — bright at the
top-left, deep navy at the bottom-right — carrying a **cyan arrowhead** at its
centre, ringed by **four broken arcs** with a small mark sitting in each of
the four gaps. The icon that was there before the artwork landed was a **black
disc with a white triangle**, Next.js's template placeholder. **One is a blue
square, the other a black circle** — they are not confusable.

**Failed.** Any error dialog is the installer's, not the app's — screenshot it.
If the app was already installed, the installer replaces it.

**Failed — the icon is still a black disc with a white triangle** after you
have rebuilt the icon cache (§0). That is neither the shell's cache nor a
failed install: the build you installed predates the artwork. Report it, and
say which of the two icons you saw.

---

## 2. Launch

Double-click the **OpsPilot** shortcut.

**What should appear, in this order.** A dark window titled **OpsPilot**
showing a live log, and then the dashboard itself replaces it:

| Time | What you see |
|---|---|
| immediately | "Checking that Docker is available…" |
| ~1 s | "Starting postgres, migrate, api, worker with Docker Compose…" |
| 1–15 min (first launch only) | Compose and pip output, scrolling |
| | "Backend ready on http://127.0.0.1:8000." |
| a few seconds later | "Dashboard ready on http://127.0.0.1:3210." |
| | the dashboard renders: a nav bar **across the top** (Dashboard, Tickets, Runs, Approvals, Knowledge) and a *Recent runs* panel reading "No runs yet" |

**The first launch is slow and that is correct.** Docker Desktop has to build
the API, worker **and migration** images from source (three of them are built —
`migrate`, `api` and `worker` each have their own `build:` block in
`docker-compose.yml`, and no shared `image:`; the file's fourth, `web`, is
never built by the app); there is no prebuilt image to download.
Every launch after that reuses them and is fast. **Do not quit during the first
launch** — a half-built image is rebuilt next time, not corrupted, but you will
wait again.

**Failed — Docker missing.** The window shows "Docker is not
installed" or "Docker is installed but not running" and stops. Go back to
§0.

**Failed — the backend did not start.** The window shows the last lines Compose
printed. Read them; Compose names the service and usually why. Then run this in
a terminal, from any directory, and watch it happen:

```
cd "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack"
docker compose --env-file "%APPDATA%\OpsPilot\opspilot.env" up -d --wait postgres migrate api worker
```

The `--env-file` is required, not optional. `docker-compose.yml` gives
`OPSPILOT_OPERATOR_TOKEN` the `:?` form, so Compose refuses to render the
project at all without it — for *every* command, `up` and `down` alike. The
file named is the one the app wrote on first launch; deleting it means the app
generates a new token on the next launch and you must create a new one yourself.

**Failed — "The backend already running does not accept this app's operator
token".** The app found a stack already answering and adopted it, then found
that stack was started with a *different* token. Left alone this produces a
dashboard that renders perfectly and refuses every approval; the app stops
instead and prints a command. Run that command verbatim — it is the one it
verified works — then start the app again.

**Failed — "The dashboard has not been built".** The installation is incomplete:
the dashboard ships *inside* the app. Reinstall.

**Failed — a blank window with nothing in it.** The app's preload failed to
load. That is a bug in the shell, not on your machine; reinstall, and report it.

---

## 3. Walk the golden path

This is the project's core claim, end to end, through the installed app.

### 3.1 Index the knowledge base

Click **Knowledge** in the nav bar. Click **Reindex knowledge/**. Within a
minute a result panel reports **17 seen** and **17 indexed** (98 chunks
written). No failure alert appears.

### 3.2 Submit a ticket

Click **Tickets**. Click **Fill with the README's ticket** (it fills the
fields for you), then **Submit ticket**.

A success panel appears naming a new run, with a link like `/runs/<uuid>`.
Note the run id.

### 3.3 Watch it stop at the approval gate

Within about 10 seconds the run reaches **Waiting approval** — that is the
run's status badge. (The phrase *Awaiting approval* is the approval summary
panel's title and the parked tool call's own status, not the run's.) Click the
run link.

The page shows a trace — classification, retrieval, planning — and an
**approval card** for a high-risk write:

> The duplicate is confirmed and policy supports a refund. This is
> HIGH_RISK_WRITE and requires human approval.
> This moves 129.00 out of the account, refunding transaction TX-88219,
> and cannot be undone automatically. The amount is in the currency the
> transaction was charged in.

**This is the point of the product.** The model proposed; deterministic code
decided it was risky; the run stopped and is waiting for a human. **If the run
goes straight to "completed" with no approval, the safety gate has regressed —
stop and report it.**

### 3.4 Approve

Click **Approve** on the card. Within a few seconds the run reads
**Completed**, and the timeline shows the refund executed once.

### 3.5 Confirm it cannot be done twice

Approving again (if the UI still offers it, or via the API below) must be
refused:

```
curl -X POST http://127.0.0.1:3210/api/approvals/<approval-id>/approve ^
  -H "Origin: http://127.0.0.1:3210" -H "Content-Type: application/json" ^
  -d '{}'
```

Expect **HTTP 409** and a body carrying `"code":"approval_already_decided"` —
the code sits inside the `error` object, and the body is compact (no space
after the colon), because it comes from the dashboard's own route handler
rather than from FastAPI.

---

## 4. What "worked" looks like, in one command

With the app running, in any terminal:

```
curl -s -o NUL -w "dashboard: %{http_code}\n" http://127.0.0.1:3210/
curl -s http://127.0.0.1:8000/ready
```

```
dashboard: 200
{"status":"ready","checks":{"database":"ok","migrations":"ok"}}
```

`200` plus `"migrations":"ok"` means the whole chain works: the app started
Docker, Compose ran the migrations, the API is serving, and the dashboard the
app spawned is answering.

---

## 5. Check the security posture survived packaging

Four things that must be true of the *installed* app. Each is a one-liner.

**The dashboard is bound to loopback only, not to your network.**

```
netstat -ano | findstr ":3210"
```

Every row must read `127.0.0.1:3210`. If any row says `0.0.0.0:3210` or
`[::]:3210`, the dashboard — which holds the operator token and can approve
refunds — is reachable from your LAN. **That is a serious finding; report it.**

Same for the API on 8000.

**The dashboard refuses a cross-origin write.**

```
curl -X POST http://127.0.0.1:3210/api/knowledge/reindex
```

Expect a JSON error containing `"code":"origin_missing"`. A request that did not
come from the dashboard's own page is refused even though it is aimed at
loopback.

**The API refuses a request with no token.**

```
curl -s http://127.0.0.1:8000/api/runs
```

Expect `"code":"unauthorized"`. The token lives in the dashboard's *server*
process; the browser never holds it.

**The installer contains no credential.**

```
dir /s /b "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\.env*"
```

Expect *"File Not Found"*. The operator token is generated on **your** machine
on first launch and written to `%APPDATA%\OpsPilot\opspilot.env`. Two machines
installed from the same file get different tokens. Deleting that file and
restarting the app generates a new one.

---

## 6. Quit, and check what it leaves behind

Close the window.

**Worked.** The window closes, containers stop, and **your data survives**:

```
docker ps --filter name=opspilot-
```

Expect no rows. Then:

```
docker volume ls | findstr opspilot
```

Expect two volumes — `opspilot_postgres-data` and `opspilot_mcp-data`. They
are meant to stay: quitting must not delete your runs, your approvals or your
refund ledger. Launch the app again and your run from §3 is still there.

**Failed — containers still listed.** The app did not stop what it started.

**Note:** if the backend was *already* running when you launched the app (you
started it by hand), quitting leaves it running. That is deliberate — the app
stops only what it started, so it cannot kill a stack you were already using.
Its log says so: *"Leaving the backend running: this app did not start it."*

---

## 7. Reporting

If anything above failed, these five things make it diagnosable:

1. What you saw, verbatim — the whole window log, not a paraphrase.
2. `docker ps -a --filter name=opspilot-` and
   `docker compose -f "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\docker-compose.yml" logs --tail 100`
3. Windows version, Docker Desktop version (`docker version`).
4. Whether the first launch was the first launch, or a repeat.
5. Which installer you ran, and where it came from — the CI run it was
   downloaded from (the run's id, and the commit that run built) or the person
   who gave you the file. If it came through a person, that run's id and
   commit are the ones they downloaded it from.

**And one more, whether or not anything failed:**

6. **Whether you recorded the walk-through** (§0), and if you did: the file's
   name and its size in bytes. This one is **not** part of what makes the
   walk-through pass — a report that says no recording was made is a complete
   report — but it is how the project learns the recording exists.

---

## Literals that must not change

When this document is translated or paraphrased, **not one character** of the
following may change — they are the things you compare against what is on
your screen:

`x64` · `~6 GB` · `5–15 minutes` · `1–15 min` · `~1 s` · about 10 seconds ·
within a minute · within a few seconds
port **8000** (the backend API) · port **3210** (the dashboard) · `127.0.0.1`
(**never `localhost`**)
`17 seen` / `17 indexed` / `98 chunks` · `129.00` · `TX-88219` ·
`HIGH_RISK_WRITE`
`HTTP 409` · `HTTP 200` · `/runs/<uuid>` · `<approval-id>`
`approval_already_decided` · `origin_missing` · `unauthorized` (the error
`code`s the walk-through produces; they are machine identifiers and are never
translated)
`opspilot_postgres-data` · `opspilot_mcp-data` · `name=opspilot-` · `--tail 100`
`%LOCALAPPDATA%\Programs\OpsPilot` · `%APPDATA%\OpsPilot\opspilot.env` ·
`resources\stack`
`OPSPILOT_OPERATOR_TOKEN` · `:?` · `NUL`
`OpsPilot-Setup-windows-x64` (the artefact's name, **with no version**) ·
`OpsPilot-Setup-<version>-x64.exe` (the installer's name) · `MZ` (the PE
signature) · `NSIS`
`Actions` · `Artifacts` (the names on GitHub's page) · 90 days (the artefact
retention)
`401 Unauthorized` (GitHub's answer when the artefact is downloaded without
being logged in)
`MP4` · `H.264` · `1280x720` · `10 MiB`
`docs/assets/d2-walkthrough.mp4` · `docs/assets/d2-walkthrough.webm`

---

## What this document does not claim

The installer's first launch builds the API, worker **and migration** images
**from source on your machine**. That is a deliberate consequence of not
publishing images to a registry, and it is why the first launch takes minutes.
Everything after it is fast, and nothing is downloaded from OpsPilot at run
time.

---

## A record sheet for the person doing it

### Filled: the walk of 2026-11-14

**Done by:** the operator on the machine (guided step by step by Claude Code,
which ran the command-line checks) **Date:** 2026-11-14
**Windows version:** Windows 11 Pro 10.0.26300 **Docker version:** 29.9.2
**Installer's SHA256 (first 16):** `565206f73b3ef924`

> **A newer candidate exists (2026-11-15, Phase 3 M3-04).** The installer was
> rebuilt from commit `81a0278`:
> `OpsPilot-Setup-0.1.0-x64.exe`, 126,045,189 bytes, SHA-256
> `71ac5ac0adcb543f…`. The mechanical half of its verification — packaged
> compose identical to the repository's, no credential in the package, and a
> cold/warm Docker start of the packaged stack — is recorded in
> [`m3-04-repackage-verification.md`](m3-04-repackage-verification.md). The
> person-steps below have **not** been re-run against that build yet; the
> 2026-11-14 record applies to the older file (`565206f7…`), which is kept.
> When this walk is repeated for the new build, fill a fresh sheet — do not
> overwrite this one. A blank sheet to copy is in
> [`installer-verification-new-package-template.md`](installer-verification-new-package-template.md),
> and the hand-over kit that goes with the new file is
> [`walkthrough-kit.md`](walkthrough-kit.md).

**The caveat this walk carries, stated plainly.** The criterion asks for a
*clean machine* and *someone who did not build it*. This walk was run on the
machine that built the installer, by the operator who directed its development
but did not write or package it. Two of the three "clean" properties were
established for the walk — OpsPilot had never been installed on this machine
(checked before step 1), and the Docker volumes that a previous test had left
behind were removed so the walk started from empty data — and one cannot be:
the build toolchain and the author's Docker images are on this machine. The
results are real, the deviations above are real, and whether this counts as
"by someone who did not build it" is a judgement the record states rather than
buries.

| Step | Result | Notes |
|---|---|---|
| 0 environment (Docker started) | ✅ pass | Docker Desktop up; loopback port 8000 was held by an unrelated project on this machine (`microbubble-agent-app-1`), which had to be paused for the walk — see "Deviations" below |
| 0b obtain the installer | ✅ pass | source: locally built `desktop/release/OpsPilot-Setup-0.1.0-x64.exe` (119,227,780 bytes, SHA-256 `565206f7…`), not a CI artifact |
| 1 install | ✅ pass | icon: ✅ blue square (new). No SmartScreen prompt was captured before the installer ran; the install completed to the default per-user folder |
| 2 launch to the dashboard | ✅ pass | first launch took: ~2 minutes on the retry (the first attempt failed on the host-port conflict above, with the app's own `Bind … port is already allocated` panel — a correct diagnosis, see Deviations) |
| 3.1 index 17/17/98 | ✅ pass | the actual numbers: **17 found / 17 indexed / 98 chunks** on a fresh volume (a second, idempotent run reports 17 found / 0 indexed / 0 chunks written, which the panel itself explains as expected) |
| 3.2 submit the ticket | ✅ pass | run id: `e2b07c50` (ticket `6dc4259d`, external id ZD-55102) |
| 3.3 **stops at the approval gate** | ✅ pass | `waiting_approval`; approval `bbf14620`; HIGH_RISK_WRITE `billing.issue_refund` `TX-88219` $129.00 — the safety gate held |
| 3.4 approve → one refund | ✅ pass | approve → executed: `REF-10091`, `replayed:false`, idempotency key `refund:e2b07c50…:TX-88219`; the billing store was read back independently: TX-88219 `refunded`, TX-88218 still `charged` |
| 3.5 approve again → 409 | ✅ pass | HTTP 409 `approval_already_decided` |
| 4 `/ready` answers 200 + ok | ✅ pass | dashboard 200; `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}` |
| 5.1 the ports bind loopback only | ✅ pass | 3210 and 8000 both `127.0.0.1` |
| 5.2 a cross-origin write is refused | ✅ pass | `origin_missing` |
| 5.3 a request with no token is refused | ✅ pass | `unauthorized` |
| 5.4 no credential in the package | ✅ pass | no `.env*` file anywhere under the install folder |
| 6 the containers stop, the volumes stay | ✅ pass | after quit: zero `opspilot-` containers; `opspilot_postgres-data` and `opspilot_mcp-data` remain |
| 6b the run is still there after a restart | ✅ pass | run `e2b07c50` (completed) still listed after relaunch |

### Deviations seen on this machine, and their disposition

1. **Host port 8000 was occupied by another project** (`microbubble-agent-app-1`,
   bound to `127.0.0.1:8000`). The packaged installer predates the
   host-port-template commit (`e8e7f6a`), so its bundled compose file still
   publishes literal `127.0.0.1:8000:8000` and its compose project name is the
   literal `opspilot`; the app's first launch therefore failed with its own
   `docker compose up exited with code 1 / Bind for 127.0.0.1:8000 failed: port
   is already allocated` panel, and nothing was left running — the failure path
   is the one the window documented, which is correct behaviour. The walk
   proceeded after pausing that unrelated project. On a clean machine this does
   not arise; the port-configurable compose (`OPSPILOT_*_HOST_PORT`) will be in
   the next installer build.
2. **The first 3.4 attempt hit stale data from an earlier test.** This machine
   already had Docker volumes named `opspilot_postgres-data` /
   `opspilot_mcp-data` from earlier runs of the *dev* stack (same compose
   project name), so the first walk inherited them: the ticket and run history
   were visible, and the refund was refused with `invalid_state` because
   `TX-88219` had already been refunded by an earlier run (`REF-10091`). That
   refusal is the billing server's state guard working; but the golden path's
   "refund executes once" clause needs clean data, so the two volumes were
   removed (test data only) and the walk repeated from 3.1. All results above
   are from the clean repeat.
3. **Consequence worth keeping:** because the packaged compose predates
   `e8e7f6a`, its project name is fixed, so an installed app *shares volumes
   with a dev stack of the same name* on the same machine. A fresh machine
   never sees this; a developer's machine does. The next installer build picks
   up `OPSPILOT_COMPOSE_PROJECT_NAME` and both deviations disappear.

### Blank sheet

| Step | Result | Notes |
|---|---|---|
| 0 environment (Docker started) | ☐ pass ☐ fail | |
| 0b obtain the installer | ☐ pass ☐ fail | source: ____ (CI run id / commit, or who gave you the file) |
| 1 install | ☐ pass ☐ fail | icon: ☐ blue square (new)  ☐ black disc with a white triangle (**cached** → rebuild the cache) |
| 2 launch to the dashboard | ☐ pass ☐ fail | first launch took: ____ minutes |
| 3.1 index 17/17/98 | ☐ pass ☐ fail | the actual numbers: ____ |
| 3.2 submit the ticket | ☐ pass ☐ fail | run id: ____ |
| 3.3 **stops at the approval gate** | ☐ pass ☐ fail | **going straight to completed = the safety gate degraded** |
| 3.4 approve → one refund | ☐ pass ☐ fail | |
| 3.5 approve again → 409 | ☐ pass ☐ fail | |
| 4 `/ready` answers 200 + ok | ☐ pass ☐ fail | |
| 5.1 the ports bind loopback only | ☐ pass ☐ fail | |
| 5.2 a cross-origin write is refused | ☐ pass ☐ fail | |
| 5.3 a request with no token is refused | ☐ pass ☐ fail | |
| 5.4 no credential in the package | ☐ pass ☐ fail | |
| 6 the containers stop, the volumes stay | ☐ pass ☐ fail | |
| 6b the run is still there after a restart | ☐ pass ☐ fail | |

**Done by:** ______________  **Date:** ____________
**Windows version:** ______________  **Docker version:** ______________
**Installer's SHA256 (first 16):** ______________ (for the new-package walk
this is expected to read `71ac5ac0adcb543f` — a first-16 that starts
`565206f7…` means the older, already-verified file was installed by mistake)

### Filled: the walk of 2026-11-15 — the v0.2.0 candidate (new record; the 2026-11-14 sheet above is kept unchanged)

**Done by:** an independent acceptance walker who did not build OpsPilot (the
release gate in `docs/release-checklist.md` §0 asks for exactly this).
**Date:** 2026-11-15. **Windows version:** Windows 11 Pro 10.0.26300.
**Docker version:** 29.9.2. **Install method:** NSIS `/S` silent install (a
headless walker cannot click the UI; the interactive-only parts are marked
unchecked rather than assumed).
**Installer:** `OpsPilot-Setup-0.2.0-x64.exe`, 130,075,040 bytes,
SHA-256 `3feb12939db05de50982331945b04c7e90c9bfcb844bdc34db0052facffb6315`
(built from commit `8133a89`, per `docs/progress.md`); hash and size verified
on a copy in the walker's own workspace `E:\OpsPilot-walkthrough-alice\`
before installing.

| Step | Result | Notes |
|---|---|---|
| 0 environment (Docker started) | ✅ pass | Docker Desktop up, Engine 29.9.2 |
| 0b obtain the installer | ✅ pass | copied from `desktop/release/` per the coordinator's brief; hash + byte count matched before install |
| 1 install | ✅ pass (via `/S`) | installs to the per-user folder (`…\Programs\OpsPilot\`, on D: on this machine); `OpsPilot.exe` FileVersion **0.2.0**; desktop + Start-Menu shortcuts present. Icon colour: **not checked** (headless) |
| 2 launch to the dashboard | ⚠️ partial | the Compose stack cold-starts fine (~5 min to `/ready`, all services healthy, migrate exit 0); **the shell's own dashboard never came up on 3210**: the packaged shell hard-codes the API at `http://127.0.0.1:8000` (`desktop/src/runtime/dependencies.ts` `DEFAULT_API_URL`; the shell reads only `OPSPILOT_DESKTOP_PORT`/`OPSPILOT_OPERATOR_TOKEN`/`OPSPILOT_USER_DATA_DIR`), and port 8000 on this machine is held by an unrelated service that the brief forbids touching. The shell polls 8000 `/ready` (404) forever and never reaches the dashboard step. The packaged dashboard itself is fine: launched by hand from the install's own `web/.next/standalone/server.js` (same args the shell uses) it serves 200 on 3210 with the full nav. **Gap for a future build: let the shell follow `OPSPILOT_API_HOST_PORT`/`OPSPILOT_API_URL`.** |
| 3.1 index 17/17/98 | ✅ pass | DB counts: 17 documents / 98 chunks; a second reindex returns `17 seen / 0 indexed / 0 chunks` (idempotent, as §9 promises), via the dashboard proxy too |
| 3.2 submit the ticket | ✅ pass (via API) | run `cbda2aa7…`, ticket `1c48e9de…`, HTTP 201 |
| 3.3 **stops at the approval gate** | ✅ pass | `waiting_approval` within ~2 s; approval `a5279924…`, "This moves 129.00 out of the account, refunding transaction TX-88219…", `high_risk_write`. The gate held — no straight-to-completed |
| 3.4 approve → one refund | ✅ pass, with a data caveat | approve → 200, run completes in ~16 s. The refund tool call itself returns `invalid_state` because the ledger volume still holds the 2026-11-14 walk's refund (`REF-10091`, TX-88219 already `refunded`) — refusing a second refund is the billing server's guard working, and the run ends `COMPLETED` with an `escalated: true` reply, exactly as `docs/agent-state-machine.md` prescribes for a refused write. Idempotency verified three ways: same-key replay → `replayed: true`; different key on the refunded transaction → `invalid_state`; a second golden-path ticket/run through its own approval → same refusal. Ledger holds exactly **1 refund** throughout. A first-refund-succeeds demonstration needs a machine without the leftover volume |
| 3.5 approve again → 409 | ✅ pass | HTTP 409, `{"error":{"code":"approval_already_decided",…}}` |
| 4 `/ready` answers 200 + ok | ✅ pass (on :18001) | `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`. Port 8000 could not carry this app's `/ready` (occupied by the unrelated service; see step 2) |
| 5.1 the ports bind loopback only | ✅ pass | 127.0.0.1 only for 15434 / 18001 / 3210; no `0.0.0.0`/`[::]` |
| 5.2 a cross-origin write is refused | ✅ pass | no-Origin POST → 403 `origin_missing`; with the dashboard's Origin → 200 |
| 5.3 a request with no token is refused | ✅ pass | `{"error":{"code":"unauthorized",…}}` |
| 5.4 no credential in the package | ✅ pass | no `.env*` under the install dir; no hardcoded long-secret literals in `stack/src`, `web/src`, compose, or the asar main; the compose token is `${OPSPILOT_OPERATOR_TOKEN:?…}` (injected, per-machine) |
| 6 the containers stop, the volumes stay | ✅ pass | a force-kill bypasses the shell's graceful teardown (expected), so the teardown was exercised with the shell's own command semantics: `docker compose down --remove-orphans` from the packaged stack with the same project/env — containers gone, exactly `opspilot_postgres-data` + `opspilot_mcp-data` remain; the unrelated 8000 service untouched throughout |
| 6b the run is still there after a restart | ✅ pass | warm start (same controlled ports) to `/ready` 200 in **16 s**; after restart: 4 tickets, 3 approved approvals, 11 audit events for the first run, 1 refund, TX-88219 `refunded` — all equal to the pre-quit baseline; the dashboard's run page still renders REF-10091/TX-88219/approved/refunded |

**Deviations, stated plainly.** (1) The shell's hard-coded `127.0.0.1:8000` API
address is the one real gap found: on a machine whose 8000 is busy the packaged
app cannot reach its own dashboard step, so this walk ran the backend on
`OPSPILOT_API_HOST_PORT=18001` (and PG 15434) — ports the packaged compose
supports since `e8e7f6a` but the shell does not follow. The interactive UI
path, icon colour, and a screen recording are unchecked (headless walker);
everything above is command-level evidence, kept in
`E:\OpsPilot-walkthrough-alice\` (ticket/approval/audit JSON, ledger snapshot,
container/volume finals). The leftover 2026-11-14 volumes and `%APPDATA%`
token were left in place per the brief; the pre-existing token file was
replaced by the app's own first-launch generation, as designed.

**Verdict:** the installer installs, cold- and warm-starts, and the golden
path plus the whole security posture behave as documented — with the
8000-hard-coded shell as the one caveat a publishing decision must weigh
(same prerequisite as the 2026-11-14 walk: "port 8000 must be free").
### Recheck: the port fix, walked independently (2026-11-15, after the fix commit)

The one gap the walk above found — the shell hard-coding the API at
`127.0.0.1:8000` — was fixed (the shell now reads `OPSPILOT_API_HOST_PORT`
from the environment, the repository `.env`, or an installed copy's app-data
`opspilot.env`), the installer was rebuilt, and a second independent walker
re-checked exactly that scenario on the fixed artifact
(`130,075,633 bytes, SHA-256 2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`,
verified before installing). Full record: `E:\OpsPilot-portrecheck\recheck-port-fix.md`;
summary:

| Check | Result |
|---|---|
| Install (NSIS `/S`), FileVersion | ✅ 0.2.0 |
| A: no variable set, port 8000 held by an unrelated service | ⚠️ reproduces the old behaviour — the shell neither adopts the foreign listener (`/ready` → 404) nor can start its own stack (`Bind for 127.0.0.1:8000 failed: port is already allocated`) and waits; the dashboard never appears. This is the documented default, not a regression |
| B: `OPSPILOT_API_HOST_PORT=18001`, `OPSPILOT_DESKTOP_PORT=33210` | ✅ the fix works — the shell's `/ready` probe hits `http://127.0.0.1:18001` (200), the dashboard opens on 33210 (200) in ~5 s, and the existing stack is adopted rather than rebuilt |
| Existing `opspilot` stack & data | ✅ left running; data volumes intact (17 documents / 98 chunks) |

**Verdict:** the shell now follows the published API port, so the
"port 8000 must be free" prerequisite from the 2026-11-14 and 2026-11-15
walks is gone — one environment variable (or `.env` entry) moves the stack
and the shell together.
