/**
 * Readiness polling: retry, give up, and stop waiting for a process that died.
 *
 * ## Why these need a fake clock at all
 *
 * `waitForHttp`'s contract is about deadlines — 300 seconds for the API, 120
 * for the dashboard — and a spec that exercised that contract against a real
 * clock would take five minutes per assertion. `dashboard-port.ts` hands its
 * poller the same treatment, and for the same reason: a test of a deadline that
 * waits out the deadline is a test nobody runs, and the deadline is the part
 * most likely to be wrong.
 *
 * The fake clock advances only when `sleep` is called, so "it retried eleven
 * times and then gave up" is an assertion about the poll loop rather than about
 * how long the machine took.
 *
 * ## The three claims
 *
 *   1. A URL that answers 2xx/3xx ends the wait; a 500 does not, because a
 *      broken dashboard waited out is reported as a slow one.
 *   2. The deadline is real, and the message names what it was waiting for and
 *      what the last answer was.
 *   3. A process that exits mid-wait ends the wait *immediately*, with a
 *      different message. `stack.ts` solved the same problem by handing the
 *      poller the `ChildProcess` it spawned, because a readiness probe
 *      satisfied by an unrelated listener is not a readiness probe.
 */

import { expect, test } from "@playwright/test";

import { StartupError } from "../src/lifecycle/errors";
import { isHttpReady, waitForHttp, waitForPortFree } from "../src/lifecycle/probe";
import { fakeClock, fakeHttp } from "./fakes";

const READY = "http://127.0.0.1:8000/ready";

test.describe("waiting for readiness", () => {
  test("returns as soon as the URL answers 2xx", async () => {
    const { probe, asked } = fakeHttp({ [READY]: 200 });
    const clock = fakeClock();

    await waitForHttp(READY, probe, clock, { timeoutMs: 300_000 });

    expect(asked).toEqual([READY]);
  });

  test("treats a redirect as ready and a 500 as not", async () => {
    const { probe } = fakeHttp({ [READY]: 302 });
    await expect(waitForHttp(READY, probe, fakeClock(), { timeoutMs: 1_000 })).resolves.toBeUndefined();

    // A dashboard that answers 500 is broken, not slow. Waiting out the
    // deadline on one would report the wrong thing to whoever reads the window.
    const broken = fakeHttp({ [READY]: 500 });
    await expect(waitForHttp(READY, broken.probe, fakeClock(), { timeoutMs: 1_000 })).rejects.toThrow(
      /answered 500/,
    );
  });

  test("retries a refused connection and succeeds on a later attempt", async () => {
    const { probe, asked } = fakeHttp({
      [READY]: (attempt) => (attempt < 3 ? new Error("ECONNREFUSED") : 200),
    });

    await waitForHttp(READY, probe, fakeClock(), { timeoutMs: 300_000 });

    expect(asked).toHaveLength(3);
  });

  test("gives up at the deadline, naming the URL and the last answer", async () => {
    const { probe, asked } = fakeHttp({ [READY]: (attempt) => (attempt < 2 ? new Error("ECONNREFUSED") : 503) });
    const clock = fakeClock();

    const failure = await waitForHttp(READY, probe, clock, { timeoutMs: 1_000, intervalMs: 500 }).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(StartupError);
    const problem = (failure as StartupError).problem;
    expect(problem.title).toBe("Something never became ready");
    expect(problem.detail).toContain(READY);
    expect(problem.detail).toContain("503");
    // Attempts at t=0, t=500ms and t=1000ms; the deadline is checked after the
    // last one, so a probe at the deadline is a last chance rather than an
    // overrun.
    expect(asked).toHaveLength(3);
  });

  test("abandons the wait at once when the process it was watching has exited", async () => {
    const { probe, asked } = fakeHttp({ [READY]: () => new Error("ECONNREFUSED") });
    const clock = fakeClock();
    let alive = true;

    const failure = await waitForHttp(READY, probe, clock, {
      timeoutMs: 300_000,
      isAlive: () => {
        const was = alive;
        alive = false;
        return was;
      },
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(StartupError);
    expect((failure as StartupError).problem.title).toBe("A process stopped before it was ready");
    // One probe, not three hundred seconds of them: a process that died at
    // start-up is a different failure from one that is slow, and reporting it
    // as slow sends whoever reads the window to the wrong place.
    expect(asked).toHaveLength(1);
  });
});

test.describe("is the API already up?", () => {
  test("a 2xx on /ready means a stack is serving", async () => {
    const { probe } = fakeHttp({ [READY]: 200 });
    expect(await isHttpReady(READY, probe)).toBe(true);
  });

  test("a 503, and a refused connection, both mean it is not", async () => {
    expect(await isHttpReady(READY, fakeHttp({ [READY]: 503 }).probe)).toBe(false);
    expect(await isHttpReady(READY, fakeHttp({ [READY]: () => new Error("ECONNREFUSED") }).probe)).toBe(false);
  });
});

test.describe("waiting for a port to be released", () => {
  test("reports the port free as soon as nothing holds it", async () => {
    let busy = true;
    const portBusy = async (): Promise<boolean> => {
      const was = busy;
      busy = false;
      return was;
    };
    expect(await waitForPortFree(3210, portBusy, fakeClock(), 15_000)).toBe(true);
  });

  test("reports the port still held when the deadline passes", async () => {
    const clock = fakeClock();
    expect(await waitForPortFree(3210, async () => true, clock, 1_000, 250)).toBe(false);
    // 1000ms of deadline at 250ms per attempt: four probes, then the answer.
    expect(clock.now()).toBeGreaterThanOrEqual(1_000);
  });
});