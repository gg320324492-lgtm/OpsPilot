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

import { StartupError } from "../src/lifecycle/errors";
import { startBackend, startDashboard } from "../src/lifecycle/session";
import { resolveOperatorToken } from "../src/lifecycle/token";
import { withCorsOrigin } from "../src/runtime/dependencies";
import {
  collector,
  fakeAuthorizedHttp,
  fakeDeps,
  fakeHttp,
  fakeRunner,
  outcome,
} from "./fakes";

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

test.describe("the token in the window", () => {
  /**
   * Every string the window can show, from the progress events and from a
   * failure the shell threw.
   *
   * The fields listed are the ones `Progress` and `UserFacing` carry that
   * hold text: a note, a log line, a reason, the three parts of a problem,
   * the endpoints and the service names. The enums (`kind`, `source`,
   * `target`, `ownership`) and the number (`seq`) cannot hold a credential.
   */
  function visibleText(
    events: readonly { note: string; line?: string; why?: string; endpoint?: string; url?: string; services?: readonly string[]; problem?: { title: string; detail: string; remedy: string } }[],
    failure?: unknown,
  ): string[] {
    const texts: string[] = [];
    for (const event of events) {
      texts.push(event.note);
      for (const value of [event.line, event.why, event.endpoint, event.url]) {
        if (value !== undefined) {
          texts.push(value);
        }
      }
      texts.push(...(event.services ?? []));
      if (event.problem !== undefined) {
        texts.push(event.problem.title, event.problem.detail, event.problem.remedy);
      }
    }
    if (failure instanceof StartupError) {
      texts.push(failure.problem.title, failure.problem.detail, failure.problem.remedy);
    }
    return texts;
  }

  test("never reaches the renderer, in any message the page can show", async () => {
    // The renderer is the untrusted side of this application: the dashboard's
    // *server* process holds the token, and the browser bundle does not. The
    // bridge hands the renderer exactly one thing — the progress history —
    // so "the renderer cannot get the token" is precisely "no progress event
    // the shell emits carries it".
    //
    // `getState()` in `src/ipc.ts` returns that history verbatim, which is
    // what makes the question checkable here: without Electron, without a
    // display and without Docker, which is where `shell.spec.ts` cannot
    // always reach.
    //
    // The paths covered are the ones that *say something*: a normal start, a
    // stack whose token does not match (the longest message the shell ever
    // writes), a Compose failure, and a dashboard that was never built.
    const token = "a-token-the-window-must-never-show";
    const env = { OPSPILOT_OPERATOR_TOKEN: token };
    const events = collector();
    const failures: unknown[] = [];

    // --- a normal start ----------------------------------------------------
    const http = fakeHttp({
      "http://127.0.0.1:8000/ready": (attempt) =>
        attempt === 1 ? new Error("ECONNREFUSED") : 200,
      "http://127.0.0.1:3210/": 200,
    });
    await startBackend(
      fakeDeps({ runner: fakeRunner(), http: http.probe, env }),
      events.emit,
    );
    await startDashboard(
      fakeDeps({ runner: fakeRunner(), http: http.probe, env }),
      events.emit,
    );

    // --- a stack this app did not start, holding a different token ---------
    failures.push(
      await startBackend(
        fakeDeps({
          runner: fakeRunner(),
          http: fakeHttp({ "http://127.0.0.1:8000/ready": 200 }).probe,
          authorizedHttp: fakeAuthorizedHttp({ "http://127.0.0.1:8000/api/runs": 401 }).probe,
          env,
        }),
        events.emit,
      ).catch((error: unknown) => error),
    );

    // --- a stack that would not come up -------------------------------------
    failures.push(
      await startBackend(
        fakeDeps({
          runner: fakeRunner({
            answers: {
              "docker compose up -d --wait --wait-timeout 300 postgres migrate api worker":
                outcome({ ok: false, status: 1, stderr: "service api failed" }),
            },
          }),
          http: fakeHttp({ "http://127.0.0.1:8000/ready": () => new Error("ECONNREFUSED") }).probe,
          env,
        }),
        events.emit,
      ).catch((error: unknown) => error),
    );

    // --- a dashboard that was never built ------------------------------------
    failures.push(
      await startDashboard(
        fakeDeps({ runner: fakeRunner(), exists: () => false, env }),
        events.emit,
      ).catch((error: unknown) => error),
    );

    // The messages the shell produced have to exist for this to be a check
    // rather than a vacuous one: a shell that said nothing would pass it.
    expect(events.events.length).toBeGreaterThan(0);
    expect(failures.filter((failure) => failure instanceof StartupError).length).toBeGreaterThan(0);

    for (const text of visibleText(events.events, ...failures)) {
      expect(
        text,
        "the operator token reached a message the window shows, and the window is the " +
          "untrusted side of this application",
      ).not.toContain(token);
    }
  });
});

test.describe("the CORS allowance this window needs", () => {  test("adds the window's own origin without dropping what was configured", () => {
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