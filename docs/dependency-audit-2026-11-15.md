# OpsPilot dependency audit — 2026-11-15

**Scope:** the tree as released (`v0.2.0`; audit run against `main@dd41d15`).
**Method:** read-only — no dependency was installed or upgraded; `pip-audit`
2.10.1 from the project `.venv` (already present, queries OSV), `npm audit
--json` in `web/` and `desktop/`, plus a static read of
`pyproject.toml`, both package manifests, the GitHub Actions workflows and
`docker-compose.yml` / Dockerfiles. No `.env` or credential was read.

## Result at a glance

| Surface | Packages | Known vulnerabilities |
|---|---:|---:|
| Python `.venv` (the project's actual runtime set) | 95 | **0** (pip-audit) |
| `web` npm dependencies | 472 | **0** (npm audit: 0 critical / 0 high / 0 moderate / 0 low) |
| `desktop` npm dependencies | 360 | **0** (npm audit: 0 / 0 / 0 / 0) |
| GitHub Actions | official actions only, current stable majors | none known |
| Docker base images | floating tags (`python:3.12-slim`, `node:22-slim`, `pgvector/pgvector:pg16`) | none known; reproducibility improvement, not a vulnerability |

## Findings by severity

- **High: none.**
- **Medium: none.**
- **Low — Python has no lockfile.** `pyproject.toml` carries floors, the
  Dockerfile describes itself as "lockfile-free", and CI resolves the newest
  compatible versions at build time. That keeps upgrades frictionless but
  makes the dependency set non-reproducible and hard to audit as a fixed
  set. If reproducibility ever matters (a longer-lived deployment, an
  external auditor), generate and commit a `uv.lock` and switch CI/Docker to
  locked installs. Recorded as the audit's single actionable improvement;
  deliberately not acted on in this release.
- **Informational:**
  - The machine-wide `py -3.12` environment (204 packages) mixes in many
    packages this project never imports (`celery`, `redis`, `torch`,
    `transformers`, `PyMuPDF`, `python-jose`, … — verified by grep against
    `src/` and `mcp_servers/`; the worker's docstring even says "no Redis,
    no Celery"). Not part of the project's attack surface; a local clean-up
    is hygiene, not a fix.
  - Base images are floating tags and the build image `uv` is 0.5.11 (old
    line) — pin digests when reproducibility matters.
  - Official actions have newer majors available (`checkout@v5`,
    `setup-python@v6`, `setup-node@v5`); routine follow-ups, no known issue
    in the versions used. Both workflows already set `contents: read`, and
    `eval-live.yml` injects its key through `env:` rather than a command
    line argument.

## Notable versions in the runtime set

`fastapi` 0.162.7, `starlette` 1.10.0, `sqlalchemy` 2.x line, `cryptography`
50.0.2, `certifi` 2026.10.23, `PyJWT` 2.15.1 (JWT handling uses PyJWT — the
project never imports `python-jose`), `next` 16.4.6, `react` 19.2.8,
`electron` 44.11.3 (no known advisory), `electron-builder` 26.18.0,
`typescript` 5.9.3, `playwright` 1.64.0. `desktop` declares no runtime
`dependencies` at all — the shell runs the packaged web build — so its
runtime surface is the narrowest of the three.

## Conclusion

**Nothing here needs action before any foreseeable use of this release.** The
known-vulnerability count is zero on every audited surface; the one
engineering improvement worth scheduling someday is the Python lockfile, and
that is a reproducibility upgrade, not a security fix.
