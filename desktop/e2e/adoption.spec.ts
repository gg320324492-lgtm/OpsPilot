/**
 * Adopting a backend this app did not start, without checking that it agrees.
 *
 * ## The failure this file was written after
 *
 * Observed on a packaged build, not theorised. A stack was left running by an
 * earlier installation — the app data folder had been reset, so the new install
 * generated a *new* operator token — and the next launch found `/ready`
 * answering, adopted the stack, and handed the dashboard a token the API had
 * never heard of. What the operator saw was a dashboard that rendered perfectly
 * and refused every approval, with nothing anywhere naming a token.
 *
 * That is the failure shape this repository keeps recording: a 200 page that
 * looks healthy. `lifecycle/token.ts` says it in so many words about the case
 * where no token exists at all; this is the case where *two different* tokens
 * exist and nothing compares them.
 *
 * ## Why the check belongs here and not in `main.ts`
 *
 * Because this is a decision about *what command to run and what to tell the
 * operator*, which is the whole subject of `src/lifecycle/`. It is also the one
 * question only this layer can answer: by the time `main.ts` has a
 * `BackendHandle`, the adoption has already happened.
 *
 * ## What each test refuses to be fooled by
 *
 *   * The refusal asserts the *rendered problem*, not merely a thrown error. An
 *     `Error("unauthorized")` in a window is not a diagnosis.
 *   * The passing cases assert the stack is still **adopted** and no command is
 *     run, so "refuse when unsure" cannot become "refuse always".
 *   * A probe that throws, and a probe that answers 404, both count as *no*
 *     disagreement — see `tokenDisagrees`'s docstring for why that is the
 *     conservative answer rather than the convenient one.
 */

import { expect, test } from "@playwright/test";

import { StartupError } from "../src/lifecycle/errors";
import { startBackend } from "../src/lifecycle/session";
import { collector, fakeAuthorizedHttp, fakeDeps, fakeHttp, fakeRunner, onlyFailure } from "./fakes";

const API_READY = "http://127.0.0.1:8000/ready";
const API_RUNS = "http://127.0.0.1:8000/api/runs";
const TOKEN = "a-token-this-app-is-about-to-use";

/** A stack that is already up, with an answer for the authenticated probe. */
function alreadyRunning(authenticated: number | Error) {
  return {
    http: fakeHttp({ [API_READY]: 200 }).probe,
    authorizedHttp: fakeAuthorizedHttp({ [API_RUNS]: authenticated }).probe,
  };
}

/** Every compose call except the `version` probe, which starts nothing. */
function stackTouchingCalls(runner: ReturnType<typeof fakeRunner>): string[][] {
  return runner.composeCalls.filter((args) => args[1] !== "version");
}

async function adopt(
  authenticated: number | Error,
  extra: { readonly env?: NodeJS.ProcessEnv; readonly composeEnvFile?: string } = {},
) {
  const runner = fakeRunner();
  const ports = alreadyRunning(authenticated);
  const events = collector();
  const deps = fakeDeps({
    runner,
    http: ports.http,
    authorizedHttp: ports.authorizedHttp,
    env: { OPSPILOT_OPERATOR_TOKEN: TOKEN, ...extra.env },
    ...(extra.composeEnvFile === undefined ? {} : { composeEnvFile: extra.composeEnvFile }),
  });
  const backend = await startBackend(deps, events.emit).catch((error: unknown) => error);
  return { backend, events, runner };
}

test.describe("adopting a backend that is already running", () => {
  test("refuses, and says why, when the running API rejects this app's token", async () => {
    const { backend, events, runner } = await adopt(401);

    expect(backend).toBeInstanceOf(StartupError);
    const problem = onlyFailure(events.events);
    expect(problem?.title).toContain("does not accept this app's operator token");
    // The remedy has to be runnable. "Something is wrong" is not a remedy, and
    // the whole point of catching this here is that the operator has one
    // command to run rather than a puzzle.
    expect(problem?.remedy).toContain("docker compose down");
    expect(problem?.remedy).toContain("/repo");
    // It must not claim it is using the backend: that line is what would send
    // an operator looking at a healthy dashboard instead of at this message.
    expect(events.events.map((event) => event.kind)).not.toContain("backend-already-running");
    // And it must not stop the stack it refused to adopt. `compose version` is
    // the one command `assessDocker` runs before anything else and touches
    // nothing, so the claim is about `up` and `down` specifically.
    expect(stackTouchingCalls(runner)).toEqual([]);
  });

  test("treats a 403 the same as a 401", async () => {
    const { backend, events } = await adopt(403);
    expect(backend).toBeInstanceOf(StartupError);
    expect(onlyFailure(events.events)?.title).toContain("operator token");
  });

  test("prints a remedy an operator can actually run", async () => {
    // `docker-compose.yml` gives OPSPILOT_OPERATOR_TOKEN the `:?` form, so
    // Compose refuses to render the project without it -- `down` included. A
    // remedy that omits it is a command that fails with an interpolation
    // error, which is the dead end this whole check exists to avoid.
    const { events } = await adopt(401, {
      composeEnvFile: "C:\\Users\\someone\\AppData\\Roaming\\OpsPilot\\opspilot.env",
    });

    const remedy = onlyFailure(events.events)?.remedy ?? "";
    expect(remedy).toContain(
      'docker compose --env-file "C:\\Users\\someone\\AppData\\Roaming\\OpsPilot\\opspilot.env" down',
    );
    expect(remedy).toContain("--remove-orphans");
    // No `-v`: stopping a stack must not be the same as deleting the operator's
    // runs, and the remedy is a command someone will paste without reading.
    expect(remedy).not.toContain("-v");
  });

  test("names no env file in a checkout, where Compose finds the repository's own", async () => {
    // The development half of the same command. `--env-file` pointing at a file
    // that does not exist there would be a second way for this remedy to fail.
    const { events } = await adopt(401);

    const remedy = onlyFailure(events.events)?.remedy ?? "";
    expect(remedy).toContain("docker compose down");
    expect(remedy).not.toContain("--env-file");
  });

  test("adopts the stack when the token is accepted", async () => {
    const { backend, events, runner } = await adopt(200);

    expect(backend).not.toBeInstanceOf(StartupError);
    expect(backend).toMatchObject({ ownership: "already-running", endpoint: "http://127.0.0.1:8000" });
    expect(events.events.map((event) => event.kind)).toContain("backend-already-running");
    // Ownership, not caution: nothing this app did not start gets touched.
    expect(stackTouchingCalls(runner)).toEqual([]);
  });

  test("does not call it a disagreement when the probe itself failed", async () => {
    // A probe that throws is a failure to *ask*, which is not evidence of a
    // disagreement. Reporting one anyway would turn a transient network blip
    // into a refusal with a confident and wrong explanation.
    const { backend, events } = await adopt(new Error("ECONNRESET"));
    expect(backend).not.toBeInstanceOf(StartupError);
    expect(onlyFailure(events.events)).toBeUndefined();
  });

  test("does not call it a disagreement on a 404, which means the route moved", async () => {
    const { backend } = await adopt(404);
    expect(backend).not.toBeInstanceOf(StartupError);
  });

  test("does not ask at all when this app has no token to ask with", async () => {
    // `main.ts` refuses to spawn anything without one, so this is unreachable
    // through the app -- and asserting it keeps the probe from becoming a
    // second, subtler way for a tokenless launch to reach Compose.
    const runner = fakeRunner();
    const authorized = fakeAuthorizedHttp({ [API_RUNS]: 401 });
    const events = collector();
    await startBackend(
      fakeDeps({ runner, http: fakeHttp({ [API_READY]: 200 }).probe, authorizedHttp: authorized.probe, env: {} }),
      events.emit,
    );
    expect(authorized.asked).toEqual([]);
  });

  test("does not ask a stack this app is starting, which agrees by construction", async () => {
    const runner = fakeRunner();
    const authorized = fakeAuthorizedHttp({ [API_RUNS]: 401 });
    const events = collector();
    const backend = await startBackend(
      fakeDeps({
        runner,
        http: fakeHttp({
          [API_READY]: (attempt) => (attempt === 1 ? new Error("ECONNREFUSED") : 200),
          "http://127.0.0.1:3210/": 200,
        }).probe,
        authorizedHttp: authorized.probe,
        env: { OPSPILOT_OPERATOR_TOKEN: TOKEN },
      }),
      events.emit,
    );

    expect(backend).toMatchObject({ ownership: "started-by-us" });
    // Compose got the token from `deps.env`, which is the same object the
    // dashboard is started from. There is no question here to ask.
    expect(authorized.asked).toEqual([]);
  });
});