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
| Docker Desktop | installed, **and started**. See below if it is not. |
| Free disk | ~6 GB. The first launch builds three container images from source. |
| Time | first launch 5–15 minutes; later launches well under a minute |

### If Docker Desktop is not installed

The app will not install Docker for you, and it will not half-work. It starts,
opens a window, and tells you exactly what is wrong:

> **Docker Desktop is not installed** — this app could not run `docker`.
> Install Docker Desktop from <https://www.docker.com/products/docker-desktop/>,
> then start this app again.

**What to do:** install Docker Desktop, start it, and wait for the whale icon
in the system tray to stop animating before launching OpsPilot. The first launch
of Docker Desktop itself can take a minute on a new machine.

If Docker Desktop is installed but **not running**, the message is different —
**"Docker is installed but not responding"** — and the remedy is to start it.
These are kept apart because "Docker isn't working" is neither of them.

### A warning you will see and can ignore

This build is **not code-signed** (no certificate was available when it was
produced). Windows SmartScreen will say *"Windows protected your PC"* or
*"Unknown publisher"*. Click **More info** → **Run anyway**. This is a
provenance warning about a download, not a detection of anything wrong with the
file.

---

## 1. Install

1. Double-click `OpsPilot-Setup-<version>-x64.exe`.
2. Choose an install folder or accept the default
   (`%LOCALAPPDATA%\Programs\OpsPilot`). Administrator rights are **not**
   required.
3. Finish.

**Worked.** The installer returns to Explorer with no error. A desktop shortcut
named **OpsPilot** and a Start-menu entry appear.

**Failed.** Any error dialog is the installer's, not the app's — screenshot it.
If the app was already installed, the installer replaces it.

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

**Failed — Docker missing.** The window shows "Docker Desktop is not
installed" or "Docker is installed but not responding" and stops. Go back to
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

If anything above failed, these four things make it diagnosable:

1. What you saw, verbatim — the whole window log, not a paraphrase.
2. `docker ps -a --filter name=opspilot-` and
   `docker compose -f "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\docker-compose.yml" logs --tail 100`
3. Windows version, Docker Desktop version (`docker version`).
4. Whether the first launch was the first launch, or a repeat.

---

## What this document does not claim

The installer's first launch builds the API and worker images **from source on
your machine**. That is a deliberate consequence of not publishing images to a
registry, and it is why the first launch takes minutes. Everything after it is
fast, and nothing is downloaded from OpsPilot at run time.