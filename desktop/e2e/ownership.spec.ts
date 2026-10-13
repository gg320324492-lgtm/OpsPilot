/**
 * The rule D1 is judged on: start what is not up, adopt what is, and stop only
 * what this app started.
 *
 * ## The claim being pinned
 *
 * "The app starts the backend if it is not running, and stops what it started on
 * quit."
 *
 * The second clause is the one that is wrong in both directions easily. An app
 * that stops an already-running stack kills a developer's work; an app that
 * stops nothing leaves containers running after a window that closed cleanly. So
 * `Ownership` is decided once, before anything is started, and every teardown
 * consults it.
 *
 * ## Why the assertions are about a recorded command list
 *
 * `stack.ts` makes the same move: `assessWorker` is a pure function precisely so
 * it can be tested against a crash-looping worker without one existing, and
 * `stack-diagnostics.spec.ts` imports it and calls it directly. Here the
 * equivalent is `fakeRunner.commands` — "quitting ran no `docker compose` at
 * all" is a statement about a list, not about a process that may or may not
 * have been running. That is the entire reason `src/lifecycle/` imports no
 * Electron and no `node:*`.
 *
 * ## What each test refuses to be fooled by
 *
 *   * The already-running case asserts `composeCalls` is **empty**, not merely
 *     free of `down`. An app that ran `compose ps` on quit would also have
 *     "left it alone" and would also have touched the developer's stack.
 *   * The started-by-us case asserts the teardown is `compose down` with **no**
 *     `-v`, because `-v` is the difference between quitting and losing data.
 *   * Both assert the progress the *window* showed, because a teardown that
 *     happens silently is indistinguishable from one that did not happen.
 */

import { expect, test } from "@playwright/test";

import { StartupError } from "../src/lifecycle/errors";
import { startApplication, startBackend, startDashboard } from "../src/lifecycle/session";
import {
  collector,
  fakeClock,
  fakeDeps,
  fakeHttp,
  fakeRunner,
  type FakeProcess,
  type FakeRunner,
  onlyFailure,
  outcome,
} from "./fakes";

const API_READY = "http://127.0.0.1:8000/ready";
const DASHBOARD_URL = "http://127.0.0.1:3210";

/** A stack that is already up: `/ready` answers on the first probe. */
function alreadyRunning() {
  return fakeHttp({ [API_READY]: 200, [`${DASHBOARD_URL}/`]: 200 });
}

/**
 * A stack that is not up yet: `/ready` refuses on the first probe and answers
 * once `compose up` has run.
 *
 * The first refusal is what makes these tests exercise the *start* path at all,
 * and getting it wrong is quiet: a fixture whose `/ready` answers immediately
 * sends every "this app started it" assertion below through the adopt path
 * instead, where nothing is started and nothing is owned.
 */
function nothingListeningYet() {
  return fakeHttp({
    [API_READY]: (attempt) => (attempt === 1 ? new Error("ECONNREFUSED") : 200),
    [`${DASHBOARD_URL}/`]: 200,
  });
}

/** Every compose call except the `version` probe, which touches nothing. */
function stackTouchingCalls(runner: ReturnType<typeof fakeRunner>): string[][] {
  return runner.composeCalls.filter((args) => args[1] !== "version");
}

test.describe("starting the backend", () => {
  test("runs `compose up` for the four services when the API is not serving", async () => {
    const runner = fakeRunner();
    const { probe, asked } = nothingListeningYet();
    const events = collector();

    const backend = await startBackend(fakeDeps({ runner, http: probe }), events.emit);

    expect(backend.ownership).toBe("started-by-us");
    expect(runner.composeCalls).toContainEqual([
      "compose",
      "up",
      "-d",
      "--wait",
      "--wait-timeout",
      "300",
      "postgres",
      "migrate",
      "api",
      "worker",
    ]);
    expect(events.events.map((event) => event.kind)).toEqual([
      "checking-docker",
      "backend-starting",
      "backend-ready",
    ]);
    // `/ready` is asked before `up` — that is the ownership decision — and again
    // after it, because `up` returning is not the API being ready.
    expect(asked.filter((url) => url === API_READY)).toHaveLength(2);
  });

  test("streams Compose's own output to the window", async () => {
    const runner = fakeRunner({
      answers: {
        "docker compose up -d --wait --wait-timeout 300 postgres migrate api worker": outcome({
          stdout: "postgres Healthy\napi Started\nworker Started",
        }),
      },
    });
    const events = collector();

    await startBackend(fakeDeps({ runner, http: nothingListeningYet().probe }), events.emit);

    // The lines are Compose's own, not a paraphrase: "the user sees progress
    // rather than a blank window" is only true if the progress is real.
    const logged = events.events.filter((event) => event.kind === "log").map((event) => event.note);
    expect(logged).toContain("postgres Healthy");
    expect(logged).toContain("worker Started");
  });

  test("a refused /ready means 'not running', so the stack is started and owned", async () => {
    const runner = fakeRunner();
    // Refused first (nobody home), then 200 once the stack is up. The opposite
    // answer would be the dangerous one: the app would adopt a stack it did not
    // start and then leave it running on quit.
    const { probe } = fakeHttp({ [API_READY]: (attempt) => (attempt === 1 ? new Error("ECONNREFUSED") : 200) });

    const backend = await startBackend(fakeDeps({ runner, http: probe }), () => undefined);

    expect(backend.ownership).toBe("started-by-us");
  });
});

test.describe("adopting a stack this app did not start", () => {
  test("runs no compose command at all, before or after", async () => {
    const runner = fakeRunner();
    const { probe, asked } = alreadyRunning();
    const events = collector();

    const backend = await startBackend(fakeDeps({ runner, http: probe }), events.emit);

    expect(backend.ownership).toBe("already-running");
    // The decisive assertion: not "no `down`", but **no call that touches the
    // stack at all**. An app that ran `compose ps` here would also have "left it
    // alone" and would also have reached for the developer's stack. The two
    // Docker *probes* still run -- they are how the app learns Docker is usable --
    // so they are excluded by name rather than by wishful thinking.
    expect(stackTouchingCalls(runner)).toEqual([]);
    expect(runner.commands.map((entry) => `${entry.command} ${entry.args.join(" ")}`)).toEqual([
      "docker info",
      "docker compose version",
    ]);
    expect(asked).toEqual([API_READY]);
    expect(events.events.map((event) => event.kind)).toEqual([
      "checking-docker",
      "backend-already-running",
    ]);
  });

  test("quitting runs no docker command and says why it did nothing", async () => {
    const runner = fakeRunner();
    const events = collector();

    const backend = await startBackend(fakeDeps({ runner, http: alreadyRunning().probe }), events.emit);
    const afterStartup = runner.commands.length;
    await backend.stop();

    // Not "no `down`" but nothing at all: tearing down an adopted stack adds no
    // command, not even one that only reads.
    expect(runner.commands.slice(afterStartup)).toEqual([]);
    const skipped = events.events.find((event) => event.kind === "teardown-skipped");
    expect(skipped?.target).toBe("backend");
    expect(skipped?.why).toContain("already answering");
  });
});

test.describe("stopping a stack this app started", () => {
  test("runs `compose down`, and keeps the volumes", async () => {
    const runner = fakeRunner();
    const events = collector();

    const backend = await startBackend(fakeDeps({ runner, http: nothingListeningYet().probe }), events.emit);
    await backend.stop();

    expect(stackTouchingCalls(runner)).toContainEqual(["compose", "down", "--remove-orphans"]);
    // The assertion that is about data rather than flags: `-v` would delete
    // `postgres-data`, so quitting the app would delete the operator's runs,
    // their approvals and their refund ledger.
    expect(stackTouchingCalls(runner).flat()).not.toContain("-v");
    const done = events.events.find((event) => event.kind === "teardown-done" && event.target === "backend");
    expect(done?.note).toContain("Your runs and approvals are kept");
  });

  test("is idempotent, because quit and close can both ask", async () => {
    const runner = fakeRunner();
    const backend = await startBackend(fakeDeps({ runner, http: nothingListeningYet().probe }), () => undefined);

    await backend.stop();
    await backend.stop();

    expect(stackTouchingCalls(runner).filter((args) => args[1] === "down")).toHaveLength(1);
  });
});

test.describe("failures the operator has to be able to read", () => {
  test("a compose failure is reported with Compose's own words, not a summary", async () => {
    const runner = fakeRunner({
      answers: {
        "docker compose up -d --wait --wait-timeout 300 postgres migrate api worker": outcome({
          ok: false,
          status: 1,
          stderr: "service api failed: container api exited (1)",
        }),
      },
    });
    const events = collector();

    const failure = await startBackend(
      fakeDeps({ runner, http: fakeHttp({ [API_READY]: () => new Error("ECONNREFUSED") }).probe }),
      events.emit,
    ).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(StartupError);
    const problem = onlyFailure(events.events);
    expect(problem?.title).toBe("The backend stack did not start");
    expect(problem?.detail).toContain("service api failed");
    expect(problem?.detail).toContain("exited (1)");
  });

  test("Docker missing stops the launch before any compose command", async () => {
    const runner = fakeRunner({
      answers: {
        "docker info": outcome({
          ok: false,
          status: null,
          spawnError: Object.assign(new Error("spawn docker ENOENT"), { code: "ENOENT" }),
        }),
      },
    });
    const events = collector();

    const failure = await startBackend(fakeDeps({ runner }), events.emit).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(StartupError);
    expect(runner.composeCalls).toEqual([]);
    expect(onlyFailure(events.events)?.title).toBe("Docker is not installed");
    // The window is told, rather than left on a spinner that never resolves.
    expect(events.events.some((event) => event.kind === "checking-docker")).toBe(true);
  });
});

test.describe("the dashboard", () => {
  test("is the standalone server, bound to loopback, on the port this app chose", async () => {
    const runner = fakeRunner();
    const { probe, asked } = fakeHttp({ [`${DASHBOARD_URL}/`]: 200 });

    const dashboard = await startDashboard(fakeDeps({ runner, http: probe }), () => undefined);

    expect(dashboard.url).toBe(DASHBOARD_URL);
    const spawned = runner.commands.find((entry) => (entry.args[0] ?? "").endsWith("standalone/server.js"));
    expect(spawned?.command).toBe("/bin/node");
    expect(spawned?.options?.cwd).toBe("/repo/web");
    // `HOSTNAME=127.0.0.1` is load-bearing: the generated `server.js` reads
    // `process.env.HOSTNAME || '0.0.0.0'`, so leaving it unset would put a
    // dashboard on every interface the machine has — reintroducing on the
    // desktop the LAN exposure P2-A1 closed on the compose-published ports.
    expect(spawned?.options?.env?.HOSTNAME).toBe("127.0.0.1");
    expect(spawned?.options?.env?.PORT).toBe("3210");
    expect(spawned?.options?.env?.NEXT_PUBLIC_API_BASE_URL).toBe("http://127.0.0.1:8000");
    expect(asked).toEqual([`${DASHBOARD_URL}/`]);
  });

  test("refuses a port something already holds, rather than adopting it", async () => {
    const runner = fakeRunner();

    const failure = await startDashboard(fakeDeps({ runner, portBusy: async () => true }), () => undefined).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(StartupError);
    const problem = (failure as StartupError).problem;
    expect(problem.title).toContain("already listening on port 3210");
    expect(problem.remedy).toContain("OPSPILOT_DESKTOP_PORT");
    // Nothing was spawned, so there is nothing that would be killed on quit.
    expect(runner.commands).toEqual([]);
  });

  test("refuses when the dashboard has not been built, and says how to build it", async () => {
    const failure = await startDashboard(fakeDeps({ runner: fakeRunner(), exists: () => false }), () => undefined).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(StartupError);
    expect((failure as StartupError).problem.title).toBe("The dashboard has not been built");
    expect((failure as StartupError).problem.remedy).toContain("npm run build");
  });

  test("a server that exits before it is ready is reported as dead, not as slow", async () => {
    const runner = fakeRunner();
    const handle = runner.spawnHanging("/bin/node", ["/repo/web/.next/standalone/server.js"]);
    // The server dies on its way up — EADDRINUSE being the case that matters.
    // `finish` resolves `exited`, which is what `startDashboard` turns into its
    // liveness check; without it the wait would run out the full 120 seconds,
    // which is the whole failure this test is about.
    handle.finish(null);

    const failure = await startDashboard(
      fakeDeps({
        runner: { ...runner, start: () => handle },
        clock: fakeClock(),
        http: fakeHttp({ [`${DASHBOARD_URL}/`]: () => new Error("ECONNREFUSED") }).probe,
      }),
      () => undefined,
    ).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(StartupError);
    expect((failure as StartupError).problem.title).toBe("A process stopped before it was ready");
  });

  test("is this app's to stop, always: the port was required to be free first", async () => {
    const base = fakeRunner();
    const handles: FakeProcess[] = [];
    const runner: FakeRunner = {
      ...base,
      start: (command, args, options) => {
        const created = base.start(command, args, options);
        handles.push(created);
        return created;
      },
    };
    const events = collector();

    const dashboard = await startDashboard(fakeDeps({ runner, http: alreadyRunning().probe }), events.emit);
    await dashboard.stop();

    // There is no "adopt an existing dashboard" path: `startDashboard` refuses a
    // port anything holds, so whatever is running afterwards is the process this
    // app started, and quitting is always entitled to stop it.
    expect(handles).toHaveLength(1);
    expect(handles[0]?.killed()).toBe(true);
    const done = events.events.find((event) => event.kind === "teardown-done" && event.target === "dashboard");
    expect(done).toBeDefined();
  });
});

test.describe("the whole application", () => {
  test("stops the dashboard before the backend", async () => {
    const base = fakeRunner();
    // One ordered log of every side effect teardown causes, so "the dashboard
    // stops first" is an assertion about a sequence rather than a comment.
    const order: string[] = [];
    const runner: FakeRunner = {
      ...base,
      run: (command, args, options) => {
        order.push(`${command} ${args.join(" ")}`);
        return base.run(command, args, options);
      },
    };
    const deps = fakeDeps({
      runner,
      http: nothingListeningYet().probe,
      portBusy: async () => {
        order.push("waited for the dashboard port");
        return false;
      },
    });

    const app = await startApplication(deps, () => undefined);
    order.length = 0;
    await app.stop();

    // The dashboard is the child process and the thing making requests. Stopping
    // it first means no request is ever served by a dashboard whose backend has
    // just vanished.
    const stop = order.indexOf("waited for the dashboard port");
    const down = order.indexOf("docker compose down --remove-orphans");
    expect(stop).toBeGreaterThanOrEqual(0);
    expect(down).toBeGreaterThan(stop);
    expect(stackTouchingCalls(base)).toContainEqual(["compose", "down", "--remove-orphans"]);
  });

  test("keeps going when the backend teardown fails, and reports it", async () => {
    const runner = fakeRunner({
      answers: { "docker compose down --remove-orphans": outcome({ ok: false, status: 1, stderr: "engine down" }) },
    });
    const events = collector();

    const app = await startApplication(fakeDeps({ runner, http: nothingListeningYet().probe }), events.emit);
    await expect(app.stop()).resolves.toBeUndefined();

    expect(stackTouchingCalls(runner)).toContainEqual(["compose", "down", "--remove-orphans"]);
    // Twice, and both on purpose: once with the raw command output, so the log
    // carries the diagnosis even if nothing is watching the failure, and once as
    // a `UserFacing` problem, which is what `Application.stop` turns an escaping
    // teardown error into so the dashboard's own teardown still ran.
    const failed = events.events.filter((event) => event.kind === "failed");
    expect(failed).toHaveLength(2);
    expect(failed.map((event) => event.note).join("\n")).toContain("`docker compose down` exited with code 1.");
    expect(failed.map((event) => event.note).join("\n")).toContain("engine down");
    expect(failed.find((event) => event.problem !== undefined)?.problem?.title).toBe(
      "The backend stack did not stop",
    );
  });

  test("an unbuilt dashboard is a named startup error, not a half-started app", async () => {
    const runner = fakeRunner();

    const failure = await startApplication(
      fakeDeps({ runner, http: nothingListeningYet().probe, exists: () => false }),
      () => undefined,
    ).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(StartupError);
    expect((failure as StartupError).problem.title).toBe("The dashboard has not been built");
  });
});