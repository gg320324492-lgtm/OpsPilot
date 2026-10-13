/**
 * The one error this package throws, and why it carries its own message.
 *
 * ## The failure D1 is judged on
 *
 * "Surfaces a real error when Docker is absent rather than a silent failure."
 *
 * A silent failure here has a specific shape, and it is the shape `web/e2e/
 * stack.ts` is built to avoid: a `docker compose` that never runs, a readiness
 * poll that spins to its deadline, and a window that either stays blank or
 * shows a spinner that never resolves — with the operator unable to tell
 * whether they forgot to install something, forgot to start something, or
 * simply have a slow machine.
 *
 * So the Docker checks run *before* anything is started, they distinguish the
 * three states an operator can actually fix, and each produces a message with
 * three parts: what is missing, what was observed, and what to install or
 * start. `UserFacing` is that message, and this error is how it reaches the
 * window.
 *
 * ## Why the wording lives here and not in the renderer
 *
 * The renderer is the untrusted side of this app (see `docs/phase2-plan.md`
 * P2-D: the server process holds a refund-capable token). The renderer is given
 * a `note` string and nothing to compute, so the wording that an operator reads
 * and the wording a test asserts are the same string, produced by the same
 * function. See `Progress` in `types.ts` for the same argument at the IPC
 * boundary.
 */

import type { DockerProblem, UserFacing } from "./types";

/** A startup failure the operator can act on. Carries no credential, by construction. */
export class StartupError extends Error {
  readonly problem: UserFacing;

  constructor(problem: UserFacing) {
    // The `message` is the same three parts, so the error is equally useful in
    // a terminal, a crash log or the window — one wording, three readers.
    super(`${problem.title}\n\n${problem.detail}\n\n${problem.remedy}`);
    this.name = "StartupError";
    this.problem = problem;
  }
}

/** Is `error` one of ours? Anything else reaching the window is a bug, not a condition. */
export function isStartupError(error: unknown): error is StartupError {
  return error instanceof StartupError;
}

/**
 * Coerce anything thrown into something the window can render.
 *
 * A `StartupError` already has its wording. Anything else has not been through
 * this review, so it is rendered as a generic failure with its message and its
 * kind — `Error` and `<something else>` say very different things about whether
 * this is worth filing.
 */
export function toUserFacing(error: unknown): UserFacing {
  if (isStartupError(error)) {
    return error.problem;
  }
  if (error instanceof Error) {
    return {
      title: "OpsPilot could not start",
      detail: `${error.name}: ${error.message}`,
      remedy:
        "The failure above is not one this app recognises. Quit and start it again; if it " +
        "repeats, run the stack by hand with `docker compose up -d --wait postgres migrate " +
        "api worker` and read what Compose prints.",
    };
  }
  return {
    title: "OpsPilot could not start",
    detail: `A non-error value was thrown: ${String(error)}`,
    remedy: "Quit and start the app again. If it repeats, this is a bug in the shell.",
  };
}

/** Clip a command's output to something a window can show without scrolling forever. */
export function excerpt(text: string, maxLines = 12): string {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
  if (lines.length === 0) {
    return "(no output)";
  }
  const kept = lines.slice(-maxLines);
  const prefix = lines.length > maxLines ? `… ${lines.length - maxLines} earlier line(s)\n` : "";
  return `${prefix}${kept.join("\n")}`;
}

/**
 * Compose a `docker-not-running` message from what the probe actually saw.
 *
 * Kept separate from the branch that decides to throw it so the exact wording
 * is testable without Docker — the branch that produces it lives in `compose.ts`
 * and needs a runner, this does not.
 */
export function dockerNotRunning(stderr: string): DockerProblem {
  return {
    kind: "docker-not-running",
    title: "Docker is installed but not running",
    detail:
      "`docker info` failed, so the Docker CLI is on PATH but no daemon answered it. That is " +
      `the state Docker Desktop is in before it finishes starting.\n\n${excerpt(stderr)}`,
    remedy:
      "Start Docker Desktop and wait for it to report that the engine is running, then open " +
      "this app again. If Docker Desktop is not installed, install it from " +
      "https://www.docker.com/products/docker-desktop/ instead.",
  };
}

export function dockerNotInstalled(command: string, code: string | undefined): DockerProblem {
  return {
    kind: "docker-not-installed",
    title: "Docker is not installed",
    detail:
      `The \`${command}\` command was not found on PATH${code === undefined ? "" : ` (${code})`}. ` +
      "OpsPilot runs its backend — PostgreSQL, the API and the worker — as Docker containers, so " +
      "there is nothing this app can start without it.",
    remedy:
      "Install Docker Desktop for Windows from https://www.docker.com/products/docker-desktop/, " +
      "start it, and open this app again. (Linux: install Docker Engine and the Compose plugin; " +
      "macOS: Docker Desktop as well.)",
  };
}

export function composePluginMissing(stderr: string): DockerProblem {
  return {
    kind: "compose-plugin-missing",
    title: "Docker Compose is not available",
    detail:
      "`docker info` succeeded — Docker is running — but `docker compose version` failed, so the " +
      "Compose plugin this app starts the stack with is missing.\n\n" +
      `${excerpt(stderr)}\n\n` +
      "Some installations ship only the older standalone `docker-compose`. This app calls " +
      "`docker compose` (the plugin form) because that is what the repository's own e2e harness " +
      "calls, and two spellings of the same tool would be two ways for the two to disagree.",
    remedy:
      "Update Docker Desktop, which bundles Compose v2, or run `docker compose install` to add " +
      "the plugin. Then open this app again.",
  };
}