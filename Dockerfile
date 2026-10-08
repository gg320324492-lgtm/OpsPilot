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

# The dependency files first, so a source-only edit does not invalidate the
# (slow) dependency-resolution layer. `pyproject.toml` is copied without the
# project source so `--no-install-project` can install dependencies alone.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev --frozen 2>/dev/null \
    || uv sync --no-install-project --no-dev

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

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen 2>/dev/null \
    || uv sync --no-dev

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

# The knowledge corpus the reindex route ingests and the eval fixtures the eval
# entry point replays. Copied because both are product data, not build input:
# `knowledge/*.md` is what `POST /api/knowledge/reindex` reads, and it lives in
# the image so a deployment does not need the repository mounted.
COPY --chown=opspilot:opspilot knowledge/ ./knowledge/

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
