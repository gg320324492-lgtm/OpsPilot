/**
 * The real `CommandRunner`, over `node:child_process`.
 *
 * ## Why `start`, and why nothing here blocks
 *
 * `docker compose up -d --wait` can take minutes — a cold image pull on a
 * machine that has never run this stack is the normal case for a first launch.
 * A `spawnSync` of that would block the Electron main process for the duration,
 * and a blocked main process cannot repaint the window, cannot answer IPC, and
 * cannot process a close request. The window would go white and stay white,
 * which is the "blank window" this app exists to replace with visible progress.
 *
 * So every command here is asynchronous and line-streaming: `start()` returns
 * immediately, `onOutput` receives each line as it arrives, and `exited` settles
 * when the process is done. Nothing in this file holds the event loop for longer
 * than one `spawn` call.
 *
 * ## The timeout exists so a wedged Docker cannot produce a blank window
 *
 * `docker info` against a Docker Desktop that is starting can block on the
 * engine's lock instead of failing. A command that never returns is the same
 * user-visible failure as one that returns the wrong thing, so every command
 * this app runs has a deadline; on expiry it is killed and the outcome says
 * `timedOut`, which `describeOutcome` renders as its own branch.
 */

import { spawn, spawnSync, type ChildProcessByStdio } from "node:child_process";
import type { Readable } from "node:stream";

import type { CommandOutcome, CommandRunner, RunOptions, StartedProcess } from "../lifecycle/types";

/** How much output is kept for the failure message. A window cannot show more. */
const MAX_RETAINED_CHARS = 16_000;

function implement(command: string, args: readonly string[], options?: RunOptions): StartedProcess {
  const opts = options ?? {};
  let stdout = "";
  let stderr = "";
  // `stdio: ["ignore", "pipe", "pipe"]` gives exactly this shape, and typing
  // it as such is what lets `child` be non-nullable below without an assertion
  // at every use.
  let child: ChildProcessByStdio<null, Readable, Readable> | undefined;
  let timedOut = false;
  let settled = false;
  let spawnError: NodeJS.ErrnoException | undefined;
  let deadline: NodeJS.Timeout | undefined;

  const keep = (which: "stdout" | "stderr", chunk: string): void => {
    const combined = `${which === "stdout" ? stdout : stderr}${chunk}`;
    const clipped = combined.length > MAX_RETAINED_CHARS ? combined.slice(combined.length - MAX_RETAINED_CHARS) : combined;
    if (which === "stdout") {
      stdout = clipped;
    } else {
      stderr = clipped;
    }
  };

  function kill(): void {
    const pid = child?.pid;
    if (child === undefined || pid === undefined) {
      return;
    }
    if (process.platform === "win32") {
      // `/T` for a child tree, `/F` to force it. `web/e2e/stack.ts::stopDashboard`
      // reaches for the same two flags and for the same reason: on Windows a
      // plain `kill()` reaches only the process it was handed, and whatever
      // holds the listening socket can outlive the quit that was supposed to
      // stop it.
      spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    } else {
      child.kill("SIGTERM");
    }
  }

  const exited = new Promise<CommandOutcome>((resolve) => {
    const finish = (status: number | null): void => {
      if (settled) {
        return;
      }
      settled = true;
      if (deadline !== undefined) {
        clearTimeout(deadline);
      }
      resolve({
        ok: !timedOut && spawnError === undefined && status === 0,
        status,
        stdout,
        stderr,
        ...(spawnError === undefined ? {} : { spawnError }),
        timedOut,
      });
    };

    const stream = (sink: "stdout" | "stderr") => (chunk: string): void => {
      keep(sink, chunk);
      if (opts.onOutput === undefined) {
        return;
      }
      for (const line of chunk.split(/\r?\n/)) {
        if (line !== "") {
          opts.onOutput(line);
        }
      }
    };

    try {
      child = spawn(command, [...args], {
        cwd: opts.cwd,
        env: opts.env,
        stdio: ["ignore", "pipe", "pipe"],
        // `shell: false` throughout. The commands here are built from literals
        // and an operator-supplied executable name — never from concatenated
        // user input — so a shell would add a metacharacter failure mode to buy
        // nothing.
        shell: false,
        windowsHide: true,
      });
    } catch (error) {
      spawnError = error as NodeJS.ErrnoException;
      finish(null);
      return;
    }

    const spawned = child;
    spawned.stdout.setEncoding("utf8");
    spawned.stderr.setEncoding("utf8");
    spawned.stdout.on("data", stream("stdout"));
    spawned.stderr.on("data", stream("stderr"));

    // A spawn error arrives asynchronously rather than as a throw: `ENOENT` is
    // reported here. This is the event that distinguishes "Docker is not
    // installed" from "Docker said no", and `assessDocker` branches on it.
    spawned.once("error", (error) => {
      spawnError = error;
      finish(null);
    });
    spawned.once("close", (code) => {
      finish(code);
    });

    if (opts.timeoutMs !== undefined && opts.timeoutMs > 0) {
      deadline = setTimeout(() => {
        timedOut = true;
        kill();
      }, opts.timeoutMs);
      // Do not hold the event loop open purely for a deadline on a command that
      // may already have exited.
      deadline.unref();
    }
  });

  return { pid: child?.pid, exited, kill };
}

/** A `CommandRunner` backed by real processes. */
export const nodeCommandRunner: CommandRunner = {
  run(command, args, options) {
    return implement(command, args, options).exited;
  },
  start(command, args, options) {
    return implement(command, args, options);
  },
};

/**
 * Is `docker` absent, or did it merely fail?
 *
 * `web/e2e/stack.ts` makes the same check for the same reason — `docker info`
 * rather than `docker --version`, because a CLI on PATH says nothing about a
 * reachable daemon — but collapses both into a skip reason. Here they are
 * different messages with different remedies, so they stay apart, and this is
 * the one place that knows how a missing executable presents itself.
 */
export function isMissingExecutable(error: NodeJS.ErrnoException | undefined): boolean {
  return error !== undefined && error.code === "ENOENT";
}