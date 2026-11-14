# Dependency health report — 2026-11-14

Scope: `pyproject.toml`/`uv.lock` (Python), `web/package.json` + lock (dashboard),
`desktop/package.json` + lock (Electron shell), container base images, GitHub
Actions runner versions. Method: `pip-audit` for Python, `npm audit` for the two
JS trees, `pip index versions` / `npm view` for staleness. All scans were run
against the working tree at `main` = `6810b4f`.

Headline: **Python is fully current with zero known vulnerabilities.** The two
JS trees carried real advisories — a **critical RCE in Next.js** (three
advisories affecting every 16.0.0–16.4.0 release) and a **critical tar
decompression DoS plus 13 high-severity findings in the desktop build
toolchain**. Both were repaired in this pass; both trees now audit clean. No
major-version upgrades were performed.

## 1. Python (pyproject.toml / uv.lock)

`pip-audit` against the resolved environment: **no known vulnerabilities**.

Every runtime dependency is at the newest published version — there is nothing
to bump, and the lockfile is young:

| Dependency | Locked | Latest | Gap | Advisory | Upgrade risk |
|---|---|---|---|---|---|
| fastapi | 0.162.7 | 0.162.7 | none | — | — |
| starlette | 1.10.0 | 1.10.0 | none | — | — |
| uvicorn[standard] | 0.54.0 | 0.54.0 | none | — | — |
| pydantic | 2.14.3 | 2.14.3 | none | — | — |
| pydantic-settings | 2.16.3 | 2.16.3 | none | — | — |
| sqlalchemy | 2.1.8 | 2.1.8 | none | — | — |
| alembic | 1.20.1 | 1.20.1 | none | — | — |
| psycopg[binary] | 3.3.9 | 3.3.9 | none | — | — |
| pgvector | 0.5.1 | 0.5.1 | none | — | — |
| mcp | 2.3.0 | 2.3.0 | none | — | — |
| httpx | 0.28.1 | 0.28.1 | none | — | — |
| python-multipart | 0.0.33 | 0.0.33 | none | — | — |
| openai | 3.51.0 | 3.51.0 | none | — | — |
| anthropic | 2.8.0 | 2.8.0 | none | — | — |
| pillow (dev) | 13.0.0 | 13.0.0 | none | — | — |
| pytest / mypy / pyyaml / cryptography / PyJWT / certifi / urllib3 | current | current | none | — | — |

Dev-tool note: **ruff 0.16.10 is one minor behind (0.17.5)**, and that is
deliberate. `pyproject.toml` pins `ruff>=0.16.10,<0.17` so the formatter's
output does not drift under `ruff format --check`; the pin and the CI gate hold
each other. Treat the 0.17 line as a planned, deliberate bump (new formatter
rev → re-format + CI), not a routine upgrade.

## 2. Web dashboard (web/package.json)

`npm audit` before: **9 findings — 1 critical, 1 high, 7 moderate.**
`npm audit` after this pass: **0 findings.**

| Package | Was | Now | Latest | Gap | Advisory | Upgrade risk |
|---|---|---|---|---|---|---|
| next | 16.3.8 | **16.4.6** | 16.4.6 | was 3 patch lines behind, carrying 3 advisories | **critical** GHSA-2464-fhr4-w9xw (unauth RCE in image optimization when SVG used), **critical** GHSA-673q-mjm6-r5xr (unauth RCE in `use cache` returning typed arrays), high GHSA-p3m5-59mp-jv5r (App Router DoS via Server Actions) | **done — same minor line, tested** |
| zod | 4.6.5 (transitive) | **4.6.7** | 4.6.7 | patch | high GHSA-mrhx-c7xc-m3w5 (memory exhaustion validating oversized constrained collections, CVSS 7.5) | **done — patch, tested** |
| minimatch (nested in typescript-estree) | 10.2.6 | **11.0.8** | 11.0.8 | 1 major on a nested transitive | moderate GHSA-g3hv-75wg-m249 (DoS via globparts amplification) | **done — dedupe override; see note** |
| eslint-config-next | ^16.3.8 | **^16.4.6** | 16.4.6 | patch | pulled the vulnerable typescript-eslint 8.71.1 line | **done — tested** |
| react / react-dom | 19.2.8 | 19.2.8 | 19.3.1 | 1 minor | none | low — defer to next framework update |
| typescript | ^5 (5.9.3) | 5.9.3 | **7.0.2** | **2 majors** | none | **watch — major; language版本升级需专项回归** |
| eslint | ^9.39.5 | 9.39.5 | **10.15.0** | **1 major** | none | watch — major |
| tailwindcss | ^4.3.3 | 4.3.3 | 4.3.5 | patch | none | low — bundle with next web change |
| @playwright/test | 1.64.0 | 1.64.0 | 1.65.1 | 1 minor | none | low |
| openapi-typescript | ^7.9.1 | 7.9.1 | 7.13.0 | 3 minors | none | low |

Next.js note: the app pins `next` exactly, so 16.3.8 had been receiving no
patch updates. 16.4.6 is the same major/minor line (16.4.x), and npm reports
`isSemVerMajor: false`; it is the fix release for all three advisories above.
The dashboard does not use `next/image` optimization or `use cache`, but the
image-optimization and Server-Action surfaces are reachable in any App Router
deploy, so the patch was applied rather than argued about.

minimatch note: the vulnerable 10.2.6 copy sat in a nested
`@typescript-eslint/typescript-estree` subtree whose `^10.2.2` range excluded
the fix. The lockfile override pins that subtree to 11.0.8 (same maintainer,
drop-in replacement; the top-level `minimatch@3.1.5` copies are outside the
advisory's `>=7.0.0` range). `npm ls` confirms one 11.0.8 instance; audit is
clean.

## 3. Desktop shell (desktop/package.json)

`npm audit` before: **24 findings — 1 critical, 13 high, 10 moderate.**
`npm audit` after this pass: **0 findings.**

| Package | Was | Now | Latest | Gap | Advisory | Upgrade risk |
|---|---|---|---|---|---|---|
| electron | 44.7.1 | **44.11.3** | 45.3.2 (major) | was several patches behind | high GHSA-jr5w-w6x2-84p9 (compromised eMudhra intermediate certificates — Chromium trust-store mitigation) | **done — same minor line; lint/typecheck green; packaged run untested locally** |
| electron-builder | ^26.0.12 → resolved 26.0.12 | **26.18.0** | 26.18.0 | 16 minors behind, dragging vulnerable build chain | the critical **tar ≤7.5.20** decompression/parse DoS (GHSA-23hp-3jrh-7fpw and siblings) plus 13 high findings (undici, cacache, make-fetch-happen, @electron/rebuild, builder-util, …) all sat in this toolchain | **done — patch-range bump; audit clean** |
| tar (transitive) | ≤7.5.20 | **7.5.23** | 7.5.23 | — | critical + 8 high | fixed via electron-builder bump |
| typescript-eslint | 8.71.1 (via eslint) | **8.72.2** | 8.72.2 | patch | moderate GHSA-g3hv chain via typescript-estree's minimatch | done |
| minimatch (nested ×3) | 9.0.9 / 10.2.6 | **11.0.8** | 11.0.8 | 1–2 majors, nested only | moderate GHSA-g3hv-75wg-m249 | done — lockfile overrides, audit clean |
| typescript | ^5 (5.9.3) | 5.9.3 | **7.0.2** | **2 majors** | none | watch — major |
| @playwright/test | 1.64.0 | 1.64.0 | 1.65.1 | 1 minor | none | low |

Desktop caveat: `electron-builder` 26.0.12→26.18.0 is within the declared
`^26.0.12` range, so no manifest change was needed for it — but the lockfile
had never been re-resolved, which is exactly how the vulnerable tar/undici
chain had fossilized. Electron 44.11.3 is the last 44.x patch; **Electron 45 is
a major upgrade** (new Chromium/Node) and is listed as a recommendation only.

## 4. Containers & toolchain

| Component | Current | Notes | Upgrade risk |
|---|---|---|---|
| `Dockerfile` (api/worker/migrate) | `python:3.12-slim` (builder + runtime) | floating tag; matches `requires-python >=3.12,<3.14` | low — keep 3.12; a 3.13 bump is already exercised by the CI test matrix |
| `web/Dockerfile` | `node:22-slim` ×3 stages | matches CI's `setup-node` 22 | low |
| `docker-compose.yml` postgres | `pgvector/pgvector:pg16` | major PG version pinned by tag | medium — a PG17/18 bump needs a dump/restore plan and a compose-level migration window; recommend watching, not bundling |
| `ghcr.io/astral-sh/uv` | `0.5.11` (pinned in Dockerfile) | old but deliberate; only resolves deps at build time | low — bump with a dedicated PR when convenient |
| GitHub Actions | `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4`, `actions/upload-artifact@v4` | **no deprecated node16 actions in use**; all current majors | low — checkout v5 / setup-node v5 exist but carry no security urgency |
| CI Python matrix | 3.12 + 3.13 | covers the declared support window | — |

## 5. Actions taken in this pass (all verified)

1. **web**: `next` 16.3.8 → 16.4.6 (critical RCE ×2 + high DoS), `zod` → 4.6.7
   (high), `eslint-config-next` → ^16.4.6, `typescript-eslint` → 8.72.2,
   nested `minimatch` → 11.0.8. **`npm audit`: 9 → 0.**
2. **desktop**: `electron` 44.7.1 → 44.11.3 (high), `electron-builder`
   lockfile re-resolve 26.0.12 → 26.18.0 (clears critical tar + 13 high),
   nested `minimatch` copies → 11.0.8. **`npm audit`: 24 → 0.**
3. Verification: web `eslint` / `tsc --noEmit` / `next build` all green;
   desktop `eslint` / `tsc --noEmit` green; Python `pytest tests/ -q`
   **1365 passed / 18 skipped**, `mypy src tests` clean (256 files),
   `ruff check .` + `ruff format --check .` clean (300 files).

## 6. Recommendations (not executed)

- **Watch / plan**: TypeScript 5→7 (both JS trees; major), ESLint 9→10 (web),
  Electron 44→45 (desktop major), React 19.2→19.3 minor, PostgreSQL 16→17/18
  image (needs data migration planning), ruff 0.16→0.17 (planned formatter
  re-pin).
- **Process**: the two JS lockfiles had drifted far behind their declared
  ranges (`^26.0.12` resolved to 26.0.12 for 16 releases). A periodic
  `npm install` + `npm audit` (e.g. monthly) would keep the fossilized-transitive
  problem from recurring; the Python side's `uv lock` is young and clean.

## Appendix — scanner output summary

- `pip-audit` (env: full dev install): `No known vulnerabilities found`
  (74 resolved packages).
- `npm audit` web, before: `9 vulnerabilities (7 moderate, 1 high, 1 critical)`;
  after: `found 0 vulnerabilities`.
- `npm audit` desktop, before: `24 vulnerabilities (10 moderate, 13 high, 1 critical)`;
  after: `found 0 vulnerabilities`.
- Key advisories addressed: GHSA-2464-fhr4-w9xw, GHSA-673q-mjm6-r5xr,
  GHSA-p3m5-59mp-jv5r (next); GHSA-mrhx-c7xc-m3w5 (zod);
  GHSA-g3hv-75wg-m249 (minimatch); GHSA-jr5w-w6x2-84p9 (electron);
  GHSA-23hp-3jrh-7fpw et al. (tar via electron-builder).
