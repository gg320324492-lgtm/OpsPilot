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

**The icon is generated, but by a person running a script rather than by
the build.** `win.icon` in `electron-builder.yml` points at `assets/icon.ico`;
the artwork behind that file is `docs/assets/icon-source.png`, a 1024×1024
export of a hand-drawn neon mark set on a dark blue rounded square, still
carrying the white canvas it was exported on.
`python desktop/scripts/make-assets.py icon` turns that export into the
`.ico`: it cuts the white ground away, crops what is left to a square,
resamples it to the ten sizes Windows may ask for — 16, 20, 24, 32, 40, 48,
64, 96, 128 and 256 — and packs them into one multi-size `.ico`. The asset
name is followed by two optional paths — the artwork to derive it from, and
the file to write — and both default to exactly the two paths above, so
that command with no paths after the asset name regenerates the committed
icon.

**The wordmark is generated too, by the same script.**
`docs/assets/logo.png` — the badge beside the name, which the root README
carries — is cropped out of `docs/assets/logo-source.png`, a 1254×1254
export of the same drawing with the lockup across the middle of a white
canvas. The result is 958×289: the export scaled to the 1024×1024 the
artwork is drawn at, then cropped to the drawing itself plus six pixels of
bare ground around it. The wordmark's white is **kept**, where the icon's
is cut away: the wordmark is a dark badge and a dark name on white, and a
transparent one would put its dark half on whatever is behind it.
`python desktop/scripts/make-assets.py wordmark` regenerates it, and
`python desktop/scripts/make-assets.py` with no arguments at all rebuilds
both artefacts.

**Both artefacts are guarded, and the guard is the script's second mode.**
`python desktop/scripts/make-assets.py --check` regenerates them into a
temporary directory and compares them with the committed files, picture
against picture and never byte against byte — a PNG's bytes depend on the
compressor that packed it, and a CI runner's compressor is not the one that
wrote the file. It writes nothing: a check that repairs the tree it is
checking is not a check. `tests/unit/test_desktop_icon_is_current.py`
asserts it, and the `lint` job in CI runs it on every push.

**It is committed rather than built, and the reason is which side of the
line the file falls on.** The repository's root `.gitignore` ignores
`build/` by bare name, so the rule reaches `desktop/build/` as well, and an
icon written there could never be committed without an exception cut out
for it; `desktop/.gitignore` is more exact about what its own rules are for
— "Build outputs of desktop/, not source" — and of the four directories it
lists, `/stage/` and `/release/` are the two that are large and
reproducible from a checkout. The icon is neither. It is artwork somebody
maintains, and the packager reads it rather than shipping it: the `files`
whitelist is `dist/**/*` and `package.json` and nothing else, so the `.ico`
never reaches the installer's file tree at all — it is compiled into the
exe's icon resource. Generating it during the build instead would have put
a Python process and Pillow between a checkout and an installer, and left
the packaging configuration pointing at a path that does not exist until
something else had run. The wordmark is committed for the same reason, and
one more: the root README references it by path, so a build-time artefact
would break the README on a fresh checkout.

**Regenerating it belongs in the same commit as the change to the
artwork.** The `.ico` is a binary, so its diff says that something changed
and not what; the artwork is the part a reviewer can read, and the two are
one decision. Committing the artwork alone is the failure this arrangement
invites — the tree then builds an installer wearing the old badge — and it
is a failure review cannot see, because the file that would show the drift
is the one nobody opens. The wordmark is the same arrangement, and the
`--check` above is what makes the omission impossible to miss.

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

## Why this window is English even when the dashboard is not

The dashboard is localised (`web/src/i18n/`; Chinese is the first translation,
English is the source locale). **This window is not, deliberately**, and the
reason is timing rather than taste.

The bootstrap page renders *before* the dashboard is reachable. It is what the
operator sees when `docker compose up` takes two minutes on a first launch, and
it is the page that has to say "Docker is not installed" when nothing will ever
come up. The dashboard's locale is a cookie in the **system browser's** profile,
set on a page this window has not reached yet and will never read — it hands the
browser off with `window.location.assign` and stops existing. So there is no
mechanism by which this window could follow the dashboard's locale, only a
mechanism by which it could be *wrong*: Electron's own `app.getLocale()` would
answer for the OS while the dashboard answers for the cookie, and one app would
speak two languages to the same person at the same moment.

Two further reasons, both about the failure path specifically:

- **These strings are the remedy.** `Install Docker Desktop and start it` is the
  instruction an operator acts on with nothing else working. It is the worst
  possible string to be reading in a second language by accident.
- **`test:lifecycle` asserts them verbatim.** The three messages above are pinned
  in `desktop/e2e/`. Localising them is possible; it is a decision with a cost,
  and this is the decision.

If this ever changes, the shape that works is: the dashboard passes its locale on
the hand-off URL, the shell reads it there and applies it only from the second
window onwards, and the *first* window stays English because it is the one that
runs before anything knows anything. That is a larger change than a translation
table and it is not this package's.

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