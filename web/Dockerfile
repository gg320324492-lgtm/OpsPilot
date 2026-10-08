# syntax=docker/dockerfile:1
#
# The dashboard image, built multi-stage with Next.js standalone output.
#
# Three stages rather than two: deps, build, runner. The dependency install is
# its own stage so that a source-only edit reuses the cached `node_modules`
# layer, and so `npm ci` (which needs `package.json` + `package-lock.json` only)
# does not re-run when a component changes.
#
# Standalone output is what makes the runtime stage small: `next build` traces
# exactly the files the server needs and writes them to `.next/standalone`,
# including a minimal `server.js`. The runtime stage copies that tree and never
# carries the dev dependencies or the full `node_modules`.
#
# The build context is the REPOSITORY ROOT (see docker-compose.yml `context: .`
# with `dockerfile: web/Dockerfile`), because the root `.dockerignore` is what
# keeps `node_modules/` and `.venv/` out of the context. Paths below are
# therefore prefixed `web/`.

# ---------------------------------------------------------------------------
# Stage 1 -- dependencies
# ---------------------------------------------------------------------------
FROM node:22-slim AS deps

WORKDIR /app/web

# `npm ci` installs exactly the lockfile, not a freshly-resolved tree, which is
# what makes the build reproducible. Copying both files first means this layer
# is cached until a dependency actually changes.
COPY web/package.json web/package-lock.json ./
RUN npm ci

# ---------------------------------------------------------------------------
# Stage 2 -- build
# ---------------------------------------------------------------------------
FROM node:22-slim AS builder

WORKDIR /app/web

COPY --from=deps /app/web/node_modules ./node_modules
COPY web/ ./

# `next build` produces `.next/standalone` because `output: "standalone"` is set
# in web/next.config.ts. NEXT_TELEMETRY_DISABLED keeps the build quiet.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 3 -- runtime
# ---------------------------------------------------------------------------
FROM node:22-slim AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    # Standalone `server.js` reads PORT and HOSTNAME. HOSTNAME must be
    # 0.0.0.0 (not the container's random hostname) or the server binds a
    # loopback-only or unresolvable name and the published port is dead.
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root, matching the Python image's posture. `node` (uid 1000) already
# exists in the base image, so it is reused rather than creating another user.
RUN groupadd --system --gid 1001 nodejs 2>/dev/null || true \
    && useradd --system --uid 1001 --gid nodejs --create-home nextjs 2>/dev/null || true

# The standalone server tree. `server.js` sits at the root of the standalone
# output and is launched by CMD below; the traced `node_modules` are inside it.
COPY --from=builder --chown=nextjs:nodejs /app/web/.next/standalone ./
# `public/` and `.next/static/` are deliberately NOT part of the standalone
# trace (Next's docs say these belong to a CDN), so they are copied in by hand.
# Without `.next/static` the page loads with no CSS or JS bundles; without
# `public` any static asset 404s. The docs' `cp -r public ... && cp -r
# .next/static ...` is what these two lines are.
COPY --from=builder --chown=nextjs:nodejs /app/web/public ./web/public
COPY --from=builder --chown=nextjs:nodejs /app/web/.next/static ./web/.next/static

# The operator token and the API base URL are NOT baked in. They are read at
# RUN TIME from the environment:
#
#   - OPSPILOT_OPERATOR_TOKEN has no NEXT_PUBLIC_ prefix and the client
#     (web/src/lib/api/client.ts) reads it in Server Components and Route
#     Handlers only. A NEXT_PUBLIC_ token would be inlined into the browser
#     bundle at BUILD time, which would publish a refund-capable credential to
#     anyone who opens the dashboard. That is why it is not an ARG or an ENV
#     here: a build-time value is a value in the image, and this one must not be.
#   - NEXT_PUBLIC_API_BASE_URL *is* inlined at build time because it is a URL,
#     not a secret, and the browser needs to know where the API is for
#     client-side fetches. Compose passes it as a build arg with a same-origin
#     default so the dashboard talks to the API through the compose network.

USER nextjs

EXPOSE 3000

# The dashboard has no dedicated health endpoint, so the check is that
# `server.js` answers the root document. `node -e` is used rather than curl
# because the slim image ships no HTTP client, and the check must not add one.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# `server.js` is the standalone entry the Next build emitted. It is run with the
# working directory the copies above placed it in.
CMD ["node", "server.js"]
