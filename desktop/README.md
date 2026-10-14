# OpsPilot desktop

The Electron shell: `docs/phase2-plan.md` **P2-D1** (shell and lifecycle) and
**P2-D2** (installer). It starts the backend, shows the operator what it is
doing, stops what it started on quit, and ships as a Windows installer.

## Running it

```bash
cd desktop
npm ci
npm run start          # builds, then launches the app
```

The window opens on a bootstrap page that streams progress while Docker Compose
brings up `postgres migrate api worker`, then hands over to the dashboard at
`http://127.0.0.1:3210`. Docker Desktop must be running. The dashboard itself
must be built first:

```bash
cd ../web && npm ci && npm run build && cd ../desktop && npm run start
```

## Packaging it

```bash
cd ../web && npm run build && cd ../desktop
npm run package        # -> release/OpsPilot-Setup-0.1.0-x64.exe  (NSIS, per-user)
npm run package:dir    # the same, unpacked, for a smoke test
```

`npm run package` is three steps: compile the shell (`tsc` + the renderer
copy), stage the runtime tree, then run `electron-builder`. What each one is
for is below; the configuration itself is `electron-builder.yml`.

**The dashboard is built before packaging, not at first run.** `next build
--output standalone` traces exactly what the server needs and emits it to
`web/.next/standalone/`; `scripts/stage-stack.mjs` copies that tree plus the
Python build context into `stage/stack/`, and `electron-builder.yml` ships it
as `resources/stack/`. The staged tree is a build output and is gitignored —
`.next/` is too — so `web/` must be built before `npm run package`, and the
staging script fails with that instruction rather than shipping nothing. A
first-run build was the alternative and is worse on every axis that matters:
it needs Node on the target machine (the plan's premise is a clean Windows box
with Docker Desktop and nothing else), it makes the user's first launch slow
and surprising, and it buys nothing — the dashboard server is already spawned
with Electron's own binary as the Node runtime via `ELECTRON_RUN_AS_NODE`, so
there is no second copy of Node anywhere.

**Two trees, two reasons.** `resources/app.asar` holds `dist/**` and nothing
else — no `e2e/`, no `scripts/`, no `node_modules` — because a whitelist is a
statement and a blacklist is a list of what someone remembered to exclude.
`resources/stack/` is real files on disk, because `docker compose` runs with it
as its working directory and `node server.js` has to `require` inside it; an
asar archive is a virtual filesystem the Node that runs the dashboard does not
have a reader for.

**The security posture is not in the packaging configuration**, and cannot be
weakened by it. `contextIsolation`, `nodeIntegration: false`, `sandbox` and
`webviewTag: false` live in `src/security-policy.ts` and are properties of the
compiled code; `desktop/e2e/security.spec.ts` asserts them against the same
module the app builds its window from. What packaging owns is the `files`
whitelist, which keeps the test runner and the build scripts out of the
archive.

**No credential ships.** `OPSPILOT_OPERATOR_TOKEN` is required by
`docker-compose.yml` with the `:?` form, which means Compose refuses to render
the project without it — so a `.env` next to the shipped compose file would be a
refund-capable credential in a file everyone who downloads the installer has.
Instead the app generates one on the target machine's first launch and writes it
to `%APPDATA%\OpsPilot\opspilot.env`. `scripts/stage-stack.mjs` walks the
staged tree and fails the build if any dotenv file is in it, because that is the
one file most likely to be added by someone who did not mean to.

**Verifying the built artefact** — including what to do when Docker Desktop is
missing — is `docs/installer-verification.md`. It is written to be followed by
someone who did not build this.

## What it owns, and what it does not

| Started by the app | Stopped when the app quits |
|---|---|
| The Compose stack, **if `/ready` was not already answering** | Yes |
| The dashboard server, always | Yes |

The first row is the whole point of `Ownership` in `src/lifecycle/session.ts`.
The decision is made once, before anything is started, by asking the API whether
it is already serving `/ready` — not by `docker compose ps`, which would not see
a stack this repository did not start. A developer with the stack already up
gets it left alone, and the window says so:

> Using the backend already running on http://127.0.0.1:8000.

`docker compose down` runs **without** `-v`. The e2e harness uses `-v` and that
is right there — a CI run wants an empty database. Here it would delete the
operator's runs, approvals and refund ledger every time they quit the app, so
the named volumes survive and only the containers go.

## When Docker is missing

The window says which of the three things is wrong and what to install, rather
than retrying or spinning:

- `Docker is not installed` — `docker` is not on PATH.
- `Docker is installed but not running` — the daemon did not answer.
- `Docker Compose is not available` — the daemon answered, `docker compose` did not.

Each carries the daemon's or Compose's own words as well, because that is the
part an operator can match against their machine.

## The security configuration

`src/security-policy.ts` holds the policy; `src/security.ts` holds the six
Electron calls that install it; `desktop/e2e/security.spec.ts` asserts the
first and `desktop/e2e/shell.spec.ts` observes it in a running window.

```ts
export const SECURE_WEB_PREFERENCES = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  webviewTag: false,
} as const;
```

Every setting matches Electron's documented default, and every one is written
out anyway — a configuration that relies on a default is a configuration whose
default can move with a version bump. Alongside them: the bootstrap page is
served from an `opspilot://` scheme with a `default-src 'none'` CSP rather than
`file://`; navigation is limited to this app's two origins by comparing parsed
origins, never prefixes; every attempt to open a second window is denied; and the
one `ipcMain.handle` validates the sender frame's origin before answering.

The rationale cites Electron's own security checklist at the pinned version
(44.7.1), item by item, in the module docstring.

## Layout, and why it is split this way

```
src/lifecycle/    the decisions — compose commands, ownership, readiness,
                  teardown, the token. Imports no Electron and no node builtins.
src/runtime/      the real implementations of those ports: child processes,
                  sockets, the filesystem.
src/security*.ts  the policy, and the Electron calls that install it.
src/preload.ts    the entire renderer bridge: two functions.
src/renderer/     the bootstrap page. Three static files, no build step.
```

`src/lifecycle/` importing nothing from the outside world is what makes the
lifecycle testable without launching a window — there is no Electron import to
stub. `web/e2e/stack.ts` draws the same line, and `desktop/e2e/` mirrors its
structure: `stack-diagnostics.spec.ts` there is `e2e/*.spec.ts` here.

## Tests

```bash
npm run test:lifecycle   # no Electron, no Docker, no browser binary
npm run test:shell       # launches the real app in a real window
```

`test:lifecycle` is what CI runs (`desktop-check`). `test:shell` needs a display
and skips cleanly without one — the ADR-0004 pattern `web/e2e/stack.ts` uses for
Docker.

## Environment

| Variable | Default | What it is for |
|---|---|---|
| `OPSPILOT_DESKTOP_PORT` | `3210` | The dashboard port. Not 3000 (the compose `web` service) and not 3100 (the e2e host dashboard) — see `dashboard-port.ts` for why those two used to collide. |
| `OPSPILOT_OPERATOR_TOKEN` | read from `.env` / `web/.env.local`, or generated | Overrides the token. `docker-compose.yml` requires it with the `:?` form, so without one Compose will not render the project at all. An **installed** copy has no `.env` to read, so `src/runtime/operator-token.ts` generates one into `%APPDATA%\OpsPilot\opspilot.env` on first launch and consults it last. |
| `OPSPILOT_CORS_ORIGINS` | appended to | The window's own origin is added, never substituted. The API's allowlist defaults to `http://localhost:3000`, which is `npm run dev`; this window is `http://127.0.0.1:3210`. |

The dashboard is served from `web/.next/standalone/server.js` with
`HOSTNAME=127.0.0.1`, because the generated server reads
`process.env.HOSTNAME || '0.0.0.0'` — leaving it unset would bind every
interface the machine has, which is the LAN exposure P2-A1 closed on the
compose-published ports.