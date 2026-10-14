/**
 * Test doubles for the lifecycle, and nothing else.
 *
 * The point of `src/lifecycle/` importing no Electron and no `node:*` is that
 * this file can stand in for the entire outside world: a command runner that
 * answers from a table, a clock that advances only when told to, an HTTP probe
 * with a scripted sequence of answers. Everything a spec asserts is then a
 * direct observation of what the lifecycle decided and in what order.
 *
 * The recording is the important half. `commands` is an ordered list of
 * `(command, args)` tuples, so "did teardown run a `docker compose down` when
 * the stack was already running?" is a question about a list, not about a
 * process that may or may not have existed.
 */

import type {
  Clock,
  CommandOutcome,
  CommandRunner,
  Progress,
  RunOptions,
  SessionDeps,
  StartedProcess,
  UserFacing,
} from "../src/lifecycle/types";

/** One command the lifecycle asked to run. */
export interface RecordedCommand {
  readonly command: string;
  readonly args: readonly string[];
  readonly options?: RunOptions;
}

export type Answer = CommandOutcome | ((options: RunOptions) => CommandOutcome);

export interface FakeRunnerOptions {
  /** Answers keyed by `"<command> <args…>"`, e.g. `"docker info"`. */
  readonly answers?: Record<string, Answer>;
  /** Used when nothing matches. Default: a clean exit. */
  readonly fallback?: Answer;
}

/** A command outcome with the boring fields filled in. */
export function outcome(partial: Partial<CommandOutcome> = {}): CommandOutcome {
  return {
    ok: partial.ok ?? true,
    status: partial.status ?? 0,
    stdout: partial.stdout ?? "",
    stderr: partial.stderr ?? "",
    ...(partial.spawnError === undefined ? {} : { spawnError: partial.spawnError }),
    timedOut: partial.timedOut ?? false,
  };
}

/** The outcome a `spawn` that could not find its executable produces. */
export function enoent(command: string): CommandOutcome {
  return outcome({
    ok: false,
    status: null,
    spawnError: Object.assign(new Error(`spawn ${command} ENOENT`), { code: "ENOENT" }),
  });
}

/**
 * `CommandRunner` with a narrowed `start`, so a spec that intercepts it knows it
 * holds a `FakeProcess` and can assert on `killed()`.
 */
export interface FakeRunner extends Omit<CommandRunner, "start"> {
  /** Every command asked for, in order. */
  readonly commands: RecordedCommand[];
  /** The args of every `docker compose …` call, in order. */
  readonly composeCalls: string[][];
  /** Lines the lifecycle reported through `onOutput`. */
  readonly output: string[];
  /** Start a command and hand back the handle; it stays alive until finished. */
  start(command: string, args: readonly string[], options?: RunOptions): FakeProcess;
  /** The same thing under a name that says what it is for. */
  spawnHanging(command: string, args: readonly string[], options?: RunOptions): FakeProcess;
}

/** A command that is still running, so a teardown can be observed killing it. */
export interface FakeProcess extends StartedProcess {
  /** Resolve `exited` as a killed process. */
  finish(status: number | null): void;
  /** Was `kill()` called? */
  readonly killed: () => boolean;
}

export function fakeRunner(options: FakeRunnerOptions = {}): FakeRunner {
  const commands: RecordedCommand[] = [];
  const output: string[] = [];

  const resolve = (command: string, args: readonly string[], runOptions?: RunOptions): CommandOutcome => {
    const key = `${command} ${args.join(" ")}`;
    const answer = options.answers?.[key] ?? options.fallback ?? outcome();
    return typeof answer === "function" ? answer(runOptions ?? {}) : answer;
  };

  const start = (command: string, args: readonly string[], runOptions?: RunOptions): FakeProcess => {
    commands.push({ command, args: [...args], ...(runOptions === undefined ? {} : { options: runOptions }) });

    let resolveExit: ((value: CommandOutcome) => void) | undefined;
    const exited = new Promise<CommandOutcome>((resolvePromise) => {
      resolveExit = resolvePromise;
    });

    const pid = 4242 + commands.length;
    let killedFlag = false;
    const process_: FakeProcess = {
      pid,
      exited,
      kill: () => {
        killedFlag = true;
        resolveExit?.(outcome({ ok: false, status: null }));
      },
      finish: (status) => {
        resolveExit?.(outcome({ ok: status === 0, status }));
      },
      killed: () => killedFlag,
    };
    void runOptions;
    return process_;
  };

  return {
    commands,
    output,
    get composeCalls() {
      return commands.filter((entry) => entry.args[0] === "compose").map((entry) => [...entry.args]);
    },
    spawnHanging: start,
    run(command, args, runOptions) {
      commands.push({ command, args: [...args], ...(runOptions === undefined ? {} : { options: runOptions }) });
      const result = resolve(command, args, runOptions);
      // Replay the scripted output through `onOutput`, so the streaming path
      // the window depends on is observable without a real subprocess.
      const text = `${result.stdout}${result.stderr}`;
      for (const line of text.split(/\r?\n/)) {
        if (line !== "") {
          output.push(line);
          runOptions?.onOutput?.(line);
        }
      }
      return Promise.resolve(result);
    },
    start,
  };
}

/**
 * A clock that does not move unless a test moves it.
 *
 * `waitForHttp` is handed `now()` and `sleep()`; with this clock, a test can
 * assert both "it retried four times" and "it gave up at the deadline" without
 * the test itself taking five minutes. `sleep` advances `now` by the requested
 * amount, which is what makes the deadline reachable at all.
 */
export function fakeClock(start = 0): Clock & { advance(ms: number): void } {
  let current = start;
  return {
    now: () => current,
    sleep: async (ms) => {
      current += ms;
    },
    advance: (ms) => {
      current += ms;
    },
  };
}

/**
 * An HTTP probe driven by a table of answers.
 *
 * `answers` is consulted by URL; `defaultStatus` answers everything else. A URL
 * may map to a status, or to a function of the attempt number — which is how a
 * spec says "refused twice, then ready", and the retry count becomes an
 * assertion.
 */
export function fakeHttp(
  answers: Record<string, number | Error | ((attempt: number) => number | Error)> = {},
): {
  probe: (url: string) => Promise<{ status: number }>;
  asked: string[];
} {
  const attempts = new Map<string, number>();
  const asked: string[] = [];
  return {
    asked,
    async probe(url: string) {
      asked.push(url);
      const attempt = (attempts.get(url) ?? 0) + 1;
      attempts.set(url, attempt);
      const answer = answers[url];
      const resolved = typeof answer === "function" ? answer(attempt) : (answer ?? 404);
      if (resolved instanceof Error) {
        throw resolved;
      }
      return { status: resolved };
    },
  };
}

/**
 * The authenticated probe, as a table of answers like `fakeHttp`.
 *
 * Defaults to answering 200 rather than to "not implemented", because a fake
 * that refused the question would make every test that does not care about it
 * look like a test about it.
 */
export function fakeAuthorizedHttp(
  answers: Record<string, number | Error | ((attempt: number) => number | Error)> = {},
): {
  probe: (url: string, token: string) => Promise<{ status: number }>;
  asked: string[];
} {
  const attempts = new Map<string, number>();
  const asked: string[] = [];
  return {
    asked,
    async probe(url: string, _token: string) {
      asked.push(url);
      const attempt = (attempts.get(url) ?? 0) + 1;
      attempts.set(url, attempt);
      const answer = answers[url];
      const resolved = typeof answer === "function" ? answer(attempt) : (answer ?? 200);
      if (resolved instanceof Error) {
        throw resolved;
      }
      return { status: resolved };
    },
  };
}

export interface FakeSessionOptions {
  readonly runner: CommandRunner;
  readonly clock?: Clock;
  readonly http?: ReturnType<typeof fakeHttp>["probe"];
  readonly authorizedHttp?: ReturnType<typeof fakeAuthorizedHttp>["probe"];
  readonly portBusy?: (port: number) => Promise<boolean>;
  readonly exists?: (path: string) => boolean;
  /**
   * The environment Compose and the dashboard inherit — and, because both are
   * started from it, the one place a test can put the operator token.
   */
  readonly env?: NodeJS.ProcessEnv;
  readonly composeEnvFile?: string;
  readonly apiUrl?: string;
  readonly dashboardPort?: number;
  readonly backendReadyTimeoutMs?: number;
  readonly dashboardReadyTimeoutMs?: number;
  readonly teardownTimeoutMs?: number;
}

/** A `SessionDeps` with every port filled, and nothing real behind it. */
export function fakeDeps(options: FakeSessionOptions): SessionDeps {
  return {
    runner: options.runner,
    clock: options.clock ?? fakeClock(),
    http: options.http ?? fakeHttp().probe,
    authorizedHttp: options.authorizedHttp ?? fakeAuthorizedHttp().probe,
    portBusy: options.portBusy ?? (async () => false),
    exists: options.exists ?? (() => true),
    repoRoot: "/repo",
    webDir: "/repo/web",
    composeEnvFile: options.composeEnvFile,
    apiUrl: options.apiUrl ?? "http://127.0.0.1:8000",
    dashboardPort: options.dashboardPort ?? 3210,
    env: options.env ?? {},
    docker: "docker",
    node: "/bin/node",
    copyDir: () => undefined,
    backendReadyTimeoutMs: options.backendReadyTimeoutMs ?? 300_000,
    dashboardReadyTimeoutMs: options.dashboardReadyTimeoutMs ?? 120_000,
    teardownTimeoutMs: options.teardownTimeoutMs ?? 60_000,
  };
}

/**
 * Collect every progress event, so a spec can assert on order and content.
 *
 * Stamps `seq` the way `main.ts` does, so a spec can assert the numbering the
 * renderer relies on rather than only the sequence of `kind`s.
 */
export function collector(): { events: Progress[]; emit: (progress: Omit<Progress, "seq">) => void } {
  const events: Progress[] = [];
  return {
    events,
    emit: (progress) => {
      events.push({ ...progress, seq: events.length });
    },
  };
}

/** The `UserFacing` a spec expects to be handed to the window. */
export function onlyFailure(events: readonly Progress[]): UserFacing | undefined {
  return events.find((event) => event.kind === "failed" || event.kind === "docker-missing")?.problem;
}