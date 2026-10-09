# syntax=docker/dockerfile:1
#
# The Python image for `api`, `worker` and the one-shot `migrate` service.
#
# One image, three run commands. The API and the worker import the same package
# and the same adapters; the migration job runs `alembic upgrade head` against
# the same code that declares the models. Building three images from one
# dependency set would triple the build time and let the three drift, and a
# migration whose models disagree with the app's is exactly the failure the
# migration step exists to prevent. The three services therefore share this
# image and differ only in `command:` in docker-compose.yml.
#
# Multi-stage: the builder resolves and installs the wheels; the runtime stage
# copies only the resulting site-packages and the source. Nothing installs at
# run time, so the runtime image needs no compiler, no pip, and no network.

# ---------------------------------------------------------------------------
# Stage 1 -- builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# `uv` is not in the base image and is fetched by the official installer. It is
# used only here: it resolves the dependency graph against a lockfile-free
# pyproject.toml far faster than pip, and its output is a normal site-packages
# tree the runtime stage can copy wholesale. The runtime stage never sees uv.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# WHY THE PROVIDER EXTRAS ARE INSTALLED HERE, AND WHY BOTH
# ------------------------------------------------------
#
# `pyproject.toml` keeps `anthropic` and `openai` in `[project.optional-dependencies]`
# so that the core workflow, the whole test suite and the golden-path demo run with
# NEITHER SDK installed. That property is about the *development and test*
# environment, and it is enforced by `tests/unit/test_layering.py::test_provider_sdk_is_imported_lazily`
# -- a static check that no module in `src/opspilot` imports either SDK at module
# scope. Installing the SDKs into a deployment image changes no import statement
# anywhere in the source, so that guard is untouched by this line.
#
# The image still needs them, because `MODEL_PROVIDER` is a RUNTIME setting
# (`docker-compose.yml` passes it through from the environment) and `fake` is only
# one of three legal values. An image built from the bare dependencies contains
# neither SDK, so every run against it died with `ModuleNotFoundError` and
# `failure_reason: "interrupted"` -- an image that cannot call a model is not a
# working deployment image.
#
# Both, rather than one, because the choice between them is made at runtime by a
# variable this build cannot see. Pinning `--extra anthropic` would ship the same
# defect one module name over: set `MODEL_PROVIDER=openai` against an
# anthropic-only image and it fails with `No module named 'openai'`. The only two
# values of `MODEL_PROVIDER` that need an SDK are `anthropic` and `openai`, so
# installing both makes every legal value of the runtime knob work against the
# image -- and makes it impossible for a build-time decision and a run-time
# setting to disagree. `fake` needs no SDK and is unaffected.
#
# No build ARG selects these on purpose. An ARG would invite an operator to build
# an image for one provider and then select the other at run time, which is the
# bug above with extra steps and a layer cache to hide it.
#
# The dependency files first, so a source-only edit does not invalidate the
# (slow) dependency-resolution layer. `pyproject.toml` is copied without the
# project source so `--no-install-project` can install dependencies alone.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev --extra anthropic --extra openai --frozen 2>/dev/null \
    || uv sync --no-install-project --no-dev --extra anthropic --extra openai

# Now the project itself. `src/` and `mcp_servers/` are the two package roots
# (pyproject `[tool.setuptools.packages.find]`). The MCP servers are imported
# in-process by the gateway (`adapters/tools/mcp_gateway.py`), so they MUST be
# in this image -- they are not separate containers.
COPY src/ ./src/
COPY mcp_servers/ ./mcp_servers/
# The migration environment and its history. `alembic.ini` at the repo root and
# `migrations/` are what `alembic upgrade head` reads; without them the migrate
# service would have nothing to run.
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Same extras as the dependency-only layer above, and for the same reason: the
# `migrate` service runs this image's project install too, and a venv that
# resolved them once must not resolve a different set the second time.
#
# The project is installed *editable*, and that is load-bearing rather than
# incidental. `fake.py::_default_fixtures_dir` resolves the fixtures directory as
# `Path(__file__).resolve().parents[4]`, i.e. "the repository root, four levels
# above the package". That arithmetic only holds while the package resolves to
# `/app/src/opspilot/...`: a non-editable install would copy the package to
# `/app/.venv/lib/python3.12/site-packages/opspilot/...`, where `parents[4]` is
# `/app/.venv/lib/python3.12` and the fixtures are found in neither place. The
# copied `/app/evals/datasets/` and the resolved `parents[4]` are the same
# contract from two ends, which is why this stays editable and why
# `fake.py` was not touched to accommodate it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --extra anthropic --extra openai --frozen 2>/dev/null \
    || uv sync --no-dev --extra anthropic --extra openai

# ---------------------------------------------------------------------------
# Stage 2 -- runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# A non-root user. The process has no reason to be root: it reads its
# configuration from the environment, writes only to the mounted MCP data
# volume, and binds an unprivileged port. Running as root would make a
# compromise of the API a compromise of the container, for no benefit.
#
# A fixed uid/gid is used rather than `useradd`'s default so a bind-mounted
# volume can be `chown`ed to a known number by an operator, and so the same
# identity holds across rebuilds.
RUN groupadd --system --gid 1001 opspilot \
    && useradd --system --uid 1001 --gid opspilot --create-home opspilot

WORKDIR /app

# The built virtualenv and the application tree, copied from the builder. The
# venv already has the entry-point scripts (`opspilot-api`, `opspilot-worker`,
# ...) on its `bin`, and pyproject's `[project.scripts]` is what put them there.
COPY --from=builder --chown=opspilot:opspilot /app/.venv /app/.venv
COPY --from=builder --chown=opspilot:opspilot /app/src /app/src
COPY --from=builder --chown=opspilot:opspilot /app/mcp_servers /app/mcp_servers
COPY --from=builder --chown=opspilot:opspilot /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=opspilot:opspilot /app/migrations /app/migrations

# The knowledge corpus the reindex route ingests and the eval data the eval
# entry point replays. Copied because both are product data, not build input:
# `knowledge/*.md` is what `POST /api/knowledge/reindex` reads, and it lives in
# the image so a deployment does not need the repository mounted.
COPY --chown=opspilot:opspilot knowledge/ ./knowledge/

# WHY `evals/datasets/` IS COPIED, AND WHY NOT THE WHOLE `evals/` TREE
# ---------------------------------------------------------------
#
# `MODEL_PROVIDER` defaults to `fake` in docker-compose.yml, so the *shipped
# default configuration* runs on `FakeModelProvider`, which replays recorded
# fixtures and has no fallback: an unmatched call raises
# `UnmatchedFixtureError` rather than inventing an answer. Those fixtures were
# not in this image, so the default could not work on any machine --
# `opspilot.adapters.models.fake.UnmatchedFixtureError: ... no recorded
# generate_structured response matched scenario 'duplicate_charge'`, and the
# first step of every run died with `failure_reason: "interrupted"`.
#
# The path is not a choice, it is a contract. `fake.py::_default_fixtures_dir`
# resolves `Path(__file__).resolve().parents[4] / "evals" / "datasets" /
# "fixtures"`, i.e. the repo root four levels above the package; from
# `/app/src/opspilot/adapters/models/fake.py` that is exactly `/app`, the
# WORKDIR. Copying the fixtures anywhere else would satisfy the COPY and still
# fail the run. `opspilot.evals.__main__` reads the same root for its datasets,
# so one copy satisfies both.
#
# `evals/datasets/`, not `evals/`: it is 36 KB against 2.6 MB for the tree, and
# the difference is `evals/results/` -- generated eval output, which
# `.dockerignore` already excludes from the build context and which must never
# be baked into an image. The datasets are the inputs the image needs; the
# results are what a previous run wrote.
COPY --chown=opspilot:opspilot evals/datasets/ ./evals/datasets/

# MCP servers write their working JSON store here. Baked as a directory owned by
# the non-root user so the server can create its file on first write without the
# container needing to run as root. Compose mounts a named volume over it, so
# the data survives a container replacement.
RUN mkdir -p /data/mcp && chown opspilot:opspilot /data/mcp

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The store location the MCP gateway's in-process servers honour
    # (`mcp_servers/_store.py::default_data_dir`). Setting it here rather than
    # only in compose means the default is correct even for a bare
    # `docker run`, and compose overrides it only to point at the volume.
    OPSPILOT_MCP_DATA_DIR=/data/mcp

USER opspilot

# No HEALTHCHECK here. The image runs three different commands, and a health
# check belongs to a *service*, not an image: `/ready` is right for the API and
# meaningless for the worker. Each service declares its own in
# docker-compose.yml, where the command it is checking is visible next to it.

# A bare `CMD` that starts the API is provided so `docker run <image>` does
# something sensible, but compose always overrides it with an explicit command.
EXPOSE 8000
CMD ["opspilot-api"]
