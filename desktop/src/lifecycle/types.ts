/**
 * The shapes the desktop lifecycle is written against, and nothing else.
 *
 * ## Why this file exists
 *
 * Every decision P2-D1 is judged on — build the Compose command, decide whether
 * the stack is already up, poll for readiness, tear down, name a missing
 * Docker — is a decision about *what command to run* and *what to tell the
 * operator*, not about windows. `web/e2e/stack.ts` draws the same line: the
 * `docker compose` arguments, the already-up decision and the teardown all live
 * in plain functions, and `BrowserWindow` never appears in that file.
 *
 * So every side effect this package performs arrives through one of the ports
 * below. Nothing under `src/lifecycle/` imports `electron`, `node:child_process`
 * or `node:net`: `src/runtime/` supplies the real implementations from a real
 * machine, and `desktop/e2e/` supplies fakes from a test. That is what makes
 * "the lifecycle is testable without launching Electron" a structural property
 * rather than an aspiration — there is no Electron import to stub.
 */

/** What a finished command left behind. Never throws; always describes. */
export interface CommandOutcome {
  /** `true` only for a clean exit (status 0, no error, not timed out). */
  readonly ok: boolean;
  /** Exit code, or `null` when the process was killed or never started. */
  readonly status: number | null;
  readonly stdout: string;
  readonly stderr: string;
  /**
   * Set when the process could not be *started* at all — `ENOENT` is the one
   * that matters, because it is how "Docker is not installed" is told apart
   * from "Docker is installed and refused". A `status` cannot express that.
   */
  readonly spawnError?: NodeJS.ErrnoException;
  /** `true` when the command was killed for exceeding `timeoutMs`. */
  readonly timedOut: boolean;
}

export interface RunOptions {
  readonly cwd?: string;
  readonly env?: NodeJS.ProcessEnv;
  /**
   * Called once per line of the command's combined output, as it arrives.
   *
   * This is how the window shows *Compose's own* progress — "postgres healthy",
   * "api started" — instead of an indeterminate spinner over a blank page. It is
   * also why `docker compose up` is not run synchronously: a synchronous spawn
   * would block the main process for the minutes that `--wait` can take, and a
   * blocked main process cannot repaint the very window that would explain the
   * wait.
   */
  readonly onOutput?: (line: string) => void;
  /** Kill the process after this long. `0`/omitted means "no limit". */
  readonly timeoutMs?: number;
}

/** A command the caller started and may still need to stop. */
export interface StartedProcess {
  readonly pid: number | undefined;
  /** Settles once, when the process exits, is killed, or times out. */
  readonly exited: Promise<CommandOutcome>;
  /** Signal the process to stop. Idempotent. */
  kill(): void;
}

export interface CommandRunner {
  /** Start `command`, wait for it, report. */
  run(command: string, args: readonly string[], options?: RunOptions): Promise<CommandOutcome>;
  /** Start `command`, hand back the handle, let the caller decide when it is done. */
  start(command: string, args: readonly string[], options?: RunOptions): StartedProcess;
}

/** The minimum of a clock the lifecycle needs: "now", and "wait". */
export interface Clock {
  now(): number;
  sleep(ms: number): Promise<void>;
}

/**
 * "Does this URL answer 2xx?" — the one readiness question, narrowed to what a
 * probe needs so a test can answer it from a table.
 */
export type HttpProbe = (url: string) => Promise<{ readonly status: number }>;

/** "Is something already listening on this port?" */
export type PortProbe = (port: number) => Promise<boolean>;

export type FileProbe = (path: string) => boolean;

/** Who owns a resource, and therefore whether quitting may stop it. */
export type Ownership = "started-by-us" | "already-running";

export type LogSource = "compose" | "dashboard";

export type TeardownTarget = "backend" | "dashboard";

export type ProgressKind =
  | "checking-docker"
  | "docker-missing"
  | "backend-already-running"
  | "backend-starting"
  | "backend-ready"
  | "dashboard-starting"
  | "dashboard-ready"
  | "log"
  | "teardown"
  | "teardown-skipped"
  | "teardown-done"
  | "failed";

/**
 * One thing the shell did, or could not do.
 *
 * **Flat, with a `note`, on purpose.** This crosses a trust boundary (it is
 * `JSON.stringify`'d into the renderer over IPC), and the renderer has exactly
 * one job: print `note`. Every extra structured field the renderer might branch
 * on is a chance for the message shown to the operator and the message asserted
 * in a test to come apart. `note` is produced *here*, in the main process, so
 * there is one definition of the wording.
 */
export interface Progress {
  readonly kind: ProgressKind;
  /**
   * A monotonically increasing number, assigned by whatever appends the event to
   * its history.
   *
   * The renderer replays the history on paint *and* subscribes to the live
   * stream, so without this the same event arrives twice and the log says so —
   * which on the failure that matters most reads as two different problems. The
   * sequence is what lets the page say "I have already drawn this one" instead
   * of guessing.
   */
  readonly seq: number;
  /** The sentence shown to the operator, verbatim. */
  readonly note: string;
  /** Set on `docker-missing` and `failed`: title, detail and remedy. */
  readonly problem?: UserFacing;
  readonly endpoint?: string;
  readonly url?: string;
  readonly ownership?: Ownership;
  readonly services?: readonly string[];
  readonly source?: LogSource;
  readonly line?: string;
  readonly target?: TeardownTarget;
  readonly why?: string;
}

/** A message an operator can act on. Never contains a credential. */
export interface UserFacing {
  readonly title: string;
  /** What was observed, in enough detail to recognise it. */
  readonly detail: string;
  /** What to do about it. */
  readonly remedy: string;
}

export type DockerProblemKind = "docker-not-installed" | "docker-not-running" | "compose-plugin-missing";

export type DockerProblem = UserFacing & { readonly kind: DockerProblemKind };

/**
 * Everything `startApplication` is allowed to touch.
 *
 * A single object rather than module-level constants, so a test can drive the
 * whole lifecycle from a table of answers. `now` and `sleep` are here for the
 * same reason `dashboard-port.ts` hands the readiness poller its own: a test
 * that has to wait out a real 300-second deadline is a test nobody runs.
 */
export interface SessionDeps {
  readonly runner: CommandRunner;
  readonly clock: Clock;
  readonly http: HttpProbe;
  readonly portBusy: PortProbe;
  readonly exists: FileProbe;
  /** The repository root — the directory holding `docker-compose.yml`. */
  readonly repoRoot: string;
  /** The `web/` directory, which holds the built dashboard. */
  readonly webDir: string;
  /** The API's published URL, as the dashboard must reach it. */
  readonly apiUrl: string;
  /** The port the desktop-hosted dashboard binds, on loopback only. */
  readonly dashboardPort: number;
  /** The environment Compose and the dashboard inherit. */
  readonly env: NodeJS.ProcessEnv;
  /** The `docker` executable, overridable for a fake or an unusual install. */
  readonly docker: string;
  /** The Node runtime used to serve the dashboard. */
  readonly node: string;
  /** Copy `public/` and `.next/static` into the standalone tree, if missing. */
  readonly copyDir?: (from: string, to: string) => void;
  /** Readiness deadline for the API after `compose up` returns. */
  readonly backendReadyTimeoutMs: number;
  /** Readiness deadline for the dashboard after its server is spawned. */
  readonly dashboardReadyTimeoutMs: number;
  /** How long a `docker compose down` may take before it is called hung. */
  readonly teardownTimeoutMs: number;
}