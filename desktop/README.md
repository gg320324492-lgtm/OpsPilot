# OpsPilot desktop

The Electron shell: `docs/phase2-plan.md` **P2-D1**. It starts the backend,
shows the operator what it is doing, and stops what it started on quit.

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
| `OPSPILOT_OPERATOR_TOKEN` | read from `.env` / `web/.env.local` | Overrides the token. `docker-compose.yml` requires it with the `:?` form, so without one Compose will not render the project at all. |
| `OPSPILOT_CORS_ORIGINS` | appended to | The window's own origin is added, never substituted. The API's allowlist defaults to `http://localhost:3000`, which is `npm run dev`; this window is `http://127.0.0.1:3210`. |

The dashboard is served from `web/.next/standalone/server.js` with
`HOSTNAME=127.0.0.1`, because the generated server reads
`process.env.HOSTNAME || '0.0.0.0'` — leaving it unset would bind every
interface the machine has, which is the LAN exposure P2-A1 closed on the
compose-published ports.