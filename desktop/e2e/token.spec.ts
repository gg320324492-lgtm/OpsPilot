/**
 * Where the operator token comes from, and the CORS allowance the window needs.
 *
 * ## Why this is worth its own file
 *
 * Both decisions here are quiet, and both fail in a way that renders a healthy
 * dashboard:
 *
 *   * A dashboard started **without** `OPSPILOT_OPERATOR_TOKEN` serves its pages
 *     perfectly — a 200, a rendered shell — and then fails every privileged
 *     write. `stack.ts` calls out exactly this shape: "the server-side API read
 *     failing is a *200 page*, not a refused connection". So the shell resolves
 *     the token before it spawns anything, and refuses to start without one.
 *   * An API whose CORS allowlist does not include the window's own origin
 *     refuses every read the browser makes directly — which is most of the
 *     dashboard — while the page itself still renders. `OPSPILOT_CORS_ORIGINS`
 *     defaults to `http://localhost:3000`, the address `npm run dev` uses, and
 *     this window is served from `http://127.0.0.1:3210`.
 *
 * Neither produces a stack trace. Both produce a screen that looks like it works
 * and is not, which is the failure mode this repository keeps recording.
 */

import { expect, test } from "@playwright/test";

import { resolveOperatorToken } from "../src/lifecycle/token";
import { withCorsOrigin } from "../src/runtime/dependencies";

test.describe("resolving the operator token", () => {
  test("prefers the environment, so an exported token actually takes effect", () => {
    const resolution = resolveOperatorToken({
      fromEnvironment: "from-shell",
      fromRepositoryEnv: "from-repo-dotenv",
      fromWebEnvLocal: "from-web-dotenv",
    });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.token).toBe("from-shell");
      expect(resolution.source).toBe("environment");
    }
  });

  test("falls back to the repository .env, which is what Compose itself reads", () => {
    const resolution = resolveOperatorToken({
      fromRepositoryEnv: "from-repo-dotenv",
      fromWebEnvLocal: "from-web-dotenv",
    });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.token).toBe("from-repo-dotenv");
      expect(resolution.source).toBe("repository .env");
    }
  });

  test("treats a blank value as absent", () => {
    // `web/.env.example` ships `OPSPILOT_OPERATOR_TOKEN=` as a placeholder.
    // Reading that as a value hands the dashboard an empty credential and
    // produces a 401 on every write instead of the instruction below.
    const resolution = resolveOperatorToken({ fromEnvironment: "   ", fromRepositoryEnv: "real-token" });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.token).toBe("real-token");
    }
  });

  test("names both what is missing and what to do when there is none", () => {
    const resolution = resolveOperatorToken({});

    expect(resolution.ok).toBe(false);
    if (!resolution.ok) {
      expect(resolution.problem.title).toBe("No operator token is configured");
      // `docker-compose.yml` uses the `:?` form, so this is not only about the
      // dashboard: Compose refuses to render the project at all without it.
      expect(resolution.problem.detail).toContain(":?");
      expect(resolution.problem.remedy).toContain("web/.env.example");
      // A credential in a message is a credential in a screenshot.
      expect(resolution.problem.remedy).not.toMatch(/[A-Za-z0-9]{24,}/);
    }
  });

  test("reports sources that disagree, because that is a 401 on every approval", () => {
    const resolution = resolveOperatorToken({
      fromRepositoryEnv: "one",
      fromWebEnvLocal: "another",
    });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      // The stack starts with one token and the dashboard proxies with the
      // other. Nothing about that failure names its cause; this does.
      expect(resolution.divergence).toEqual(["web/.env.local"]);
    }
  });

  test("says nothing about divergence when the sources agree", () => {
    const resolution = resolveOperatorToken({
      fromRepositoryEnv: "same",
      fromWebEnvLocal: "same",
    });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.divergence).toBeUndefined();
    }
  });
});

test.describe("the CORS allowance this window needs", () => {
  test("adds the window's own origin without dropping what was configured", () => {
    expect(withCorsOrigin("http://localhost:3000", "http://127.0.0.1:3210")).toBe(
      "http://localhost:3000,http://127.0.0.1:3210",
    );
  });

  test("is idempotent, so a second launch does not grow the list", () => {
    const once = withCorsOrigin("http://localhost:3000", "http://127.0.0.1:3210");
    expect(withCorsOrigin(once, "http://127.0.0.1:3210")).toBe(once);
  });

  test("does not treat a blank setting as an empty entry in the list", () => {
    expect(withCorsOrigin("", "http://127.0.0.1:3210")).toBe("http://127.0.0.1:3210");
    expect(withCorsOrigin(undefined, "http://127.0.0.1:3210")).toBe("http://127.0.0.1:3210");
    expect(withCorsOrigin("http://localhost:3000,,  ", "http://127.0.0.1:3210")).toBe(
      "http://localhost:3000,http://127.0.0.1:3210",
    );
  });

  test("adds a loopback origin and never a wildcard", () => {
    const result = withCorsOrigin("http://localhost:3000", "http://127.0.0.1:3210");
    // `OPSPILOT_CORS_ORIGINS` refuses `*` outright (settings.py), and the
    // desktop build must not be the thing that introduces one.
    expect(result).not.toContain("*");
    for (const origin of result.split(",")) {
      expect(origin.startsWith("http://127.0.0.1:") || origin.startsWith("http://localhost:")).toBe(true);
    }
  });
});