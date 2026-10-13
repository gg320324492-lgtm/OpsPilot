/**
 * The e2e harness's own diagnostics, tested without Docker.
 *
 * ## Why this file is not a browser test
 *
 * Two of the pieces of instrumentation `stack.ts` puts around a Docker stack
 * make no Docker calls at all once they are written down: `assessWorker` is a
 * pure function of (log text, container state), and `composeDiagnosticsDir` is
 * a pure function of (spec name). Both are guards whose entire value is in what
 * they refuse to accept, and both have already failed in the way that matters:
 *
 *   * `assessWorker`'s predecessor returned success for a **crash-looping**
 *     worker, because the boot line of a dead attempt is still sitting in an
 *     append-only log -- and it was verified by injecting that crash loop and
 *     watching `--wait` call the stack healthy. A guard that has to be
 *     exercised against the thing it guards is not much of a guard if the
 *     harness cannot bring the thing up.
 *   * `composeDiagnosticsDir`'s predecessor wrote both specs' captures into one
 *     directory as six fixed filenames, so a full run left the worker's log
 *     reading `(no output)` -- measured, on green runs as well as red ones --
 *     because the spec that sorted last had no worker in its stack.
 *
 * So both are pinned here, as decisions over literal inputs, in the same
 * `npm run e2e` job as the walkthrough and on the same machine a developer
 * edits them on. Following `write-origin.spec.ts`: the module under test is
 * imported and called directly, so "the guard rejected it" is an observation
 * rather than an inference from a container's mood.
 *
 * Importing `./stack` runs its module-level `findRepoRoot()`, which walks up
 * from the cwd looking for `docker-compose.yml`. That is a `fs` call and
 * nothing else -- no `docker` process is spawned at import time -- so this file
 * runs unchanged on a machine with no daemon at all, exactly as
 * `write-origin.spec.ts` does.
 *
 * ## The two claims, and what a reader should take from a green run here
 *
 *  1. A worker that printed its boot line and then died is **not** polling, and
 *     the test says so with that exact log present, so it cannot pass by
 *     accident through the log being empty.
 *  2. Two specs' captures cannot land on each other, and both land where the
 *     `if: failure()` artifact upload already looks.
 */

import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  assessWorker,
  composeDiagnosticsDir,
  describeWorkerState,
  DIAGNOSTIC_SERVICES,
  REPO_ROOT,
  type WorkerContainerState,
} from "./stack";

// ---------------------------------------------------------------------------
// Fixtures: the three container shapes these tests argue about, as literals.
// ---------------------------------------------------------------------------

/** A worker container Docker is happy with: up, never restarted. */
const HEALTHY: WorkerContainerState = {
  found: true,
  status: "running",
  restarting: false,
  exitCode: 0,
  restarts: 0,
};

/** A `restart: unless-stopped` worker mid-crash-loop, as `docker inspect` reports it. */
const CRASH_LOOPING: WorkerContainerState = {
  found: true,
  status: "restarting",
  restarting: true,
  exitCode: 1,
  restarts: 6,
};

/** Booted once, then died and stayed dead (`restart` not in force, or the window before it). */
const EXITED_AFTER_BOOTING: WorkerContainerState = {
  found: true,
  status: "exited",
  restarting: false,
  exitCode: 1,
  restarts: 0,
};

/** Compose reports no worker container at all -- `ps -aq worker` came back empty. */
const ABSENT: WorkerContainerState = {
  found: false,
  status: "",
  restarting: false,
  exitCode: 0,
  restarts: 0,
};

/** The worker's one boot line, verbatim from `worker/__main__.py`'s `_run`. */
const BOOT_LINE =
  "opspilot-worker[worker-compose]: booted; marked 0 interrupted run(s) failed; polling every 1.0s";

/**
 * The log of a worker that boots, prints the boot line, and is then killed --
 * every attempt retained, because a container's log is append-only and
 * `compose logs` prints the lot. Reproduced on this machine with the worker's
 * command overridden outside the repository.
 *
 * The timestamps are increasing and each attempt repeats the same fatal line,
 * which is what makes this *not* something a human skimming it would read as
 * healthy: it is the shape that `--wait` reported as `Healthy`.
 */
const BOOT_THEN_CRASH_LOG = [
  "worker-1  | 2026-10-13T04:14:40.011Z " + BOOT_LINE,
  "worker-1  | 2026-10-13T04:14:40.480Z opspilot-worker: refusing to start -- injected fault",
  "worker-1  | 2026-10-13T04:14:41.004Z " + BOOT_LINE,
  "worker-1  | 2026-10-13T04:14:41.612Z opspilot-worker: refusing to start -- injected fault",
  "worker-1  | 2026-10-13T04:14:42.221Z " + BOOT_LINE,
  "worker-1  | 2026-10-13T04:14:42.798Z opspilot-worker: refusing to start -- injected fault",
].join("\n");

/** A healthy worker's entire lifetime: one line, and nothing has gone wrong. */
const HEALTHY_LOG = "worker-1  | 2026-10-13T04:14:40.011Z " + BOOT_LINE;

/** A container that is running and has said nothing about itself. */
const SILENT_LOG = "worker-1  | 2026-10-13T04:14:40.011Z starting up";

/**
 * Narrow a verdict to a failure, so each test below reads as one claim rather
 * than as three assertions that can be satisfied by a passing `polling: true`.
 */
function failure(verdict: ReturnType<typeof assessWorker>): {
  cause: "unhealthy_container" | "silent";
  stateLine: string;
} {
  expect(verdict.polling, `expected a failure verdict, got ${JSON.stringify(verdict)}`).toBe(false);
  if (verdict.polling) {
    throw new Error("unreachable: the assertion above already failed");
  }
  return verdict;
}

// ---------------------------------------------------------------------------
// The restart guard.
// ---------------------------------------------------------------------------

test.describe("the worker's own account of itself, read against Docker's", () => {
  /**
   * The defect, stated as a test.
   *
   * The log here is not missing its boot line -- it has three, one per attempt,
   * and the test says so before it asks anything else. So the only reason
   * `assessWorker` can answer "not polling" here is that it read the container's
   * state. Remove the `restarts === 0` clause and this fails; move the log check
   * back in front of the state check and this fails. Either of those was the
   * previous implementation, and it passed this worker through.
   */
  test("a worker that booted and then crash-looped is caught, boot line and all", () => {
    // Stated as its own assertion so the test cannot go green by accident if the
    // fixture is ever edited into a log that has no boot line in it -- which
    // would make it a test of the easy half and nothing else.
    expect(BOOT_THEN_CRASH_LOG).toContain("booted;");
    expect(BOOT_THEN_CRASH_LOG.match(/booted;/g)).toHaveLength(3);

    const verdict = failure(assessWorker(BOOT_THEN_CRASH_LOG, CRASH_LOOPING));

    expect(
      verdict.cause,
      "a crash-looping worker must be reported as an unhealthy container, not as one that " +
        "has merely not spoken yet -- the second reads as 'slow', the first is the failure.",
    ).toBe("unhealthy_container");
    // And the line it reports has to carry the two facts that distinguish it
    // from a healthy worker. A message that said only "container restarting"
    // would have left a reader to go and find the restart count themselves.
    expect(verdict.stateLine).toContain("RESTARTING");
    expect(verdict.stateLine).toContain("6 restart(s)");
  });

  /**
   * The same shape one step earlier: it has not been restarted yet, so
   * `RestartCount` is zero and only the *status* gives it away.
   *
   * This is why the check is not `restarts > 0` alone. Under
   * `restart: unless-stopped` there is a window between the process dying and
   * Docker's restart policy acting; in it the container reads `exited` with a
   * restart count of zero.
   */
  test("a worker that booted and then exited is caught before Docker has counted a restart", () => {
    expect(EXITED_AFTER_BOOTING.restarts).toBe(0);
    const verdict = failure(assessWorker(BOOT_LINE, EXITED_AFTER_BOOTING));

    expect(verdict.cause).toBe("unhealthy_container");
    expect(verdict.stateLine).toContain("container exited");
  });

  test("no worker container at all is caught", () => {
    const verdict = failure(assessWorker(BOOT_LINE, ABSENT));

    expect(verdict.cause).toBe("unhealthy_container");
    expect(verdict.stateLine).toContain("no worker container");
  });

  /**
   * The half the guard must not swallow.
   *
   * A running container with a clean restart count that has not printed its
   * boot line is a *different* failure from a container that cannot be one, and
   * the two get different messages. Collapsing them would report "no worker
   * container" for a worker that is running and merely early, which is the
   * version of this guard that is wrong in the other direction.
   */
  test("a running worker that has not spoken yet is a different failure from a broken container", () => {
    const verdict = failure(assessWorker(SILENT_LOG, HEALTHY));

    expect(verdict.cause).toBe("silent");
    expect(verdict.stateLine).toContain("container running");
    expect(verdict.stateLine).toContain("0 restart(s)");
  });

  /**
   * The positive case, and the reason the guard is free: on a healthy stack
   * `restarts` is zero and the boot line is present, so this costs one log read
   * and one `docker inspect` and never fails.
   */
  test("a healthy worker's boot line with a clean restart count is polling", () => {
    expect(assessWorker(HEALTHY_LOG, HEALTHY)).toEqual({ polling: true });
  });

  test("a boot line with a timestamp and an id the regex accepts is still the same claim", () => {
    // The regex is anchored on nothing but the message text, so a worker that
    // names itself differently (`WORKER_ID` is deployment-configurable) must not
    // fall through the healthy path by accident. This is the shape compose
    // prints with a multi-worker setup.
    const verdict = assessWorker(
      "worker-1  | 2026-10-13T04:14:40.011Z " +
        "opspilot-worker[host-a-2c1f]: booted; marked 1 interrupted run(s) failed; polling every 1.0s",
      HEALTHY,
    );
    expect(verdict).toEqual({ polling: true });
  });
});

/**
 * The state line is one definition.
 *
 * It is now returned by `assessWorker`, printed in both `waitForWorkerPolling`
 * failures and in `workerReport`'s header, so a reader comparing a thrown error
 * against the report attached to a test failure is comparing two renderings of
 * the same fact. They have to agree, and the restart count is the part that
 * carries the evidence.
 */
test.describe("the worker state line", () => {
  test("names the status, the restart flag, the exit code and the restart count together", () => {
    expect(describeWorkerState(CRASH_LOOPING)).toBe(
      "container restarting, RESTARTING (exit code 1, 6 restart(s))",
    );
    expect(describeWorkerState(HEALTHY)).toBe("container running (exit code 0, 0 restart(s))");
  });

  test("says the container is absent rather than describing a status it does not have", () => {
    // `""`/`0` are the placeholders `workerContainerState` returns when Compose
    // lists no container; rendering them would print "container  (exit code 0,
    // 0 restart(s))", which reads as a real measurement rather than an absence.
    expect(describeWorkerState(ABSENT)).toBe(
      "no worker container (compose reports no worker container at all)",
    );
  });
});

// ---------------------------------------------------------------------------
// The capture's path.
// ---------------------------------------------------------------------------

test.describe("where a spec's compose capture is written", () => {
  /**
   * The defect, stated as a test: one directory per capture.
   *
   * `web-service.spec.ts` sorts after `golden-path.spec.ts` and its stack has no
   * worker, so when both wrote `test-results/compose-logs/worker.log`, the last
   * writer always won and the run's worker log always read `(no output)`.
   */
  test("two specs' captures are different directories, so neither can overwrite the other", () => {
    const golden = composeDiagnosticsDir("golden-path");
    const webService = composeDiagnosticsDir("web-service");

    expect(golden, "both captures resolved to one path; the second spec erases the first").not.toBe(
      webService,
    );
    // Not merely unequal: each is inside the other only by accident, and neither
    // is a parent of the other, so a reader walking the tree reaches both.
    expect(golden.includes(webService)).toBe(false);
    expect(webService.includes(golden)).toBe(false);
  });

  test("every spec's capture carries its own name, at the end of the path", () => {
    // A reader of a CI artifact sees paths before contents. The name has to be
    // the last segment -- visible in the file listing, without opening anything.
    for (const label of ["golden-path", "failure-states", "web-service"] as const) {
      expect(path.basename(composeDiagnosticsDir(label))).toBe(label);
    }
  });

  test("the capture still lands under web/test-results/, where the failure upload looks", () => {
    // `.github/workflows/ci.yml`'s `web-e2e` job uploads `web/test-results/`
    // wholesale with `if: failure()`. A capture written anywhere else is a
    // capture that never reaches the artefact a reader opens.
    const expectedRoot = path.join(REPO_ROOT, "web", "test-results");
    for (const label of ["golden-path", "failure-states", "web-service"] as const) {
      const dir = composeDiagnosticsDir(label);
      const relative = path.relative(expectedRoot, dir);
      expect(
        relative.startsWith("..") || path.isAbsolute(relative),
        `${label}'s capture went to ${dir}, outside ${expectedRoot}. The failure upload ` +
          `only picks up web/test-results/, so a capture elsewhere is a capture nobody reads.`,
      ).toBe(false);
    }
    // And at the documented depth, so the shape a reader learns from one run is
    // the shape the next one has.
    expect(path.basename(path.dirname(composeDiagnosticsDir("golden-path")))).toBe("compose-logs");
  });

  test("the write cannot escape its own directory, because the label cannot be a path", () => {
    // Not a security boundary -- every label is a literal in this repository --
    // but the property is what makes the join above safe to trust, and it is
    // cheap to state. `DiagnosticLabel` is a closed union, so there is no
    // runtime value that could carry a separator.
    expect(composeDiagnosticsDir("golden-path")).toBe(
      path.join(REPO_ROOT, "web", "test-results", "compose-logs", "golden-path"),
    );
  });

  test("each capture holds the whole stack's services, including the ones it never started", () => {
    // The worker log is the file this whole exercise exists for, and the `web`
    // log is the file `web-service.spec.ts`'s failures need. Both are captured
    // by both specs; only one of each exists per run, and an empty file for the
    // service that was not started is the honest answer.
    expect([...DIAGNOSTIC_SERVICES]).toContain("worker");
    expect([...DIAGNOSTIC_SERVICES]).toContain("web");
    // `ps -a` rather than `ps`: the exited worker is the one whose state is
    // wanted, and the default `ps` does not list it.
    expect(DIAGNOSTIC_SERVICES.length).toBe(5);
  });
});