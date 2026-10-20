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

**Option A — you have a GitHub account: download it from CI.** Every push
builds the installer and attaches it to that run. The `desktop-package` job of
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

**There is no GitHub Release.** Do not go looking for one: this repository has
no tag and no release page, and the README links to no download. The artifact
is a build product attached to one run: it expires, and it is indexed nowhere.

**If the file you were given was built on somebody's own machine, know what
you are holding.** It is a build of a **working tree**, not of a **commit**:
the build output is gitignored, no commit in the repository records it, and
nothing inside the file says which source tree produced it. It is a perfectly
installable file with no traceable provenance behind it, where the CI artifact
has a run and a commit behind it. **Prefer the CI artifact** — and if you are
not sure which of the two you have been given, ask.

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
opens a window, and tells you exactly what is wrong:

> **Docker is not installed** — this app could not run `docker`.
> Install Docker Desktop from <https://www.docker.com/products/docker-desktop/>,
> then start this app again.

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
| | the dashboard renders: a left-hand nav (Tickets, Runs, Knowledge, Approvals) and an empty ticket list |

**The first launch is slow and that is correct.** Docker Desktop has to build
the API and worker images from source; there is no prebuilt image to download.
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

Click **Knowledge** in the left nav. Click **Reindex knowledge**. Within a
minute a result panel reports **17 seen** and **17 indexed** (98 chunks
written). No failure alert appears.

### 3.2 Submit a ticket

Click **Tickets**. Click **Fill with the README** (it fills the fields for
you), then **Submit ticket**.

A success panel appears naming a new run, with a link like `/runs/<uuid>`.
Note the run id.

### 3.3 Watch it stop at the approval gate

Within about 10 seconds the run reaches **Awaiting approval**. Click the run
link.

The page shows a trace — classification, retrieval, planning — and an
**approval card** for a high-risk write:

> The duplicate is confirmed and policy supports a refund. This is
> HIGH_RISK_WRITE and requires human approval.
> This moves $129.00 USD out of the account, refunding transaction TX-88219,
> and cannot be undone automatically.

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

Expect **HTTP 409** and `"code": "approval_already_decided"`.

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

## What this document does not claim

The installer's first launch builds the API and worker images **from source on
your machine**. That is a deliberate consequence of not publishing images to a
registry, and it is why the first launch takes minutes. Everything after it is
fast, and nothing is downloaded from OpsPilot at run time.