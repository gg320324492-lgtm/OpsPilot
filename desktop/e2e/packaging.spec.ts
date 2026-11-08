/**
 * The two questions a packaged app cannot answer the way a checkout answers
 * them: where the stack is, and where the operator token is.
 *
 * ## Why these two and nothing else
 *
 * The rest of the shell is layout-independent — `lifecycle/` reaches the disk
 * through `SessionDeps` and reaches Docker through a command runner, so the
 * lifecycle tests drive all of it from a table of answers. What is *not*
 * layout-independent is the answer to "which directory", and there were two of
 * them:
 *
 *   * `findRepoRoot` walks up from `process.cwd()`. For a double-clicked
 *     installer that is `C:\Windows\System32`, so a packaged launch found no
 *     `docker-compose.yml` and told the operator to start the app from inside a
 *     repository they did not have. Nothing in the existing suite caught it,
 *     because every test passes a `repoRoot` explicitly.
 *   * The token came from the environment, the repository `.env`, or
 *     `web/.env.local`. All three are files in a checkout. An installed app has
 *     no checkout, and shipping a `.env` would publish a refund-capable
 *     credential to everyone who downloads the installer.
 *
 * So both are asserted here against the real filesystem rather than a fake,
 * because the thing being tested *is* a filesystem lookup. A fake here would
 * assert that the code called the function it was written to call.
 */

import { expect, test } from "@playwright/test";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { resolveOperatorToken } from "../src/lifecycle/token";
import { readEnvValue, resolveStackRoot, STACK_MARKER } from "../src/runtime/dependencies";
import { ensureOperatorToken, TOKEN_ENV_FILENAME, TOKEN_ENV_KEY } from "../src/runtime/operator-token";

/** The repository root -- the directory that holds `pyproject.toml`. */
const REPO_ROOT = path.resolve(__dirname, "..", "..");

/**
 * Where this project's throwaway files go: inside the repository, under its
 * own gitignored `.opspilot/` directory. The system temp directory is where
 * they used to go, and a run that failed part-way left them there -- 140
 * `opspilot-packaging-*` directories on this machine before the path changed.
 */
const SCRATCH_DIR =
  process.env["OPSPILOT_SCRATCH_DIR"] ?? path.join(REPO_ROOT, ".opspilot", "tmp");

/**
 * Where this project's throwaway files go: inside the repository, under its
 * own gitignored `.opspilot/` directory. The system temp directory is where
 * they used to go, and a run that failed part-way left them there -- 140
 * `opspilot-packaging-*` directories on this machine before the path changed.
 */
/** A directory that is thrown away afterwards, so a failure leaves nothing behind. */
function scratch(): string {
  mkdirSync(SCRATCH_DIR, { recursive: true });
  return mkdtempSync(path.join(SCRATCH_DIR, "opspilot-packaging-"));
}

/** A directory that holds `docker-compose.yml`, which is what makes it a stack root. */
function fakeStackRoot(): string {
  const dir = scratch();
  writeFileSync(path.join(dir, STACK_MARKER), "name: opspilot\n", "utf8");
  return dir;
}

/**
 * Does `directory`, or any ancestor of it, hold the stack marker?
 *
 * The walk is the one `findRepoRoot` performs, so the answer is about the
 * filesystem rather than about a guess.
 */
function hasStackMarkerAbove(directory: string): boolean {
  let current = directory;
  for (;;) {
    if (existsSync(path.join(current, STACK_MARKER))) {
      return true;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return false;
    }
    current = parent;
  }
}

/**
 * A directory that is outside every repository: no ancestor of it holds a
 * `docker-compose.yml`.
 *
 * The candidates are the roots of the drives this machine has, and each one is
 * **checked** rather than assumed: a drive root that happened to hold a
 * `docker-compose.yml` would make the test below pass for the wrong reason,
 * which is the failure mode this file exists to catch.
 */
function directoryOutsideAnyRepository(): string {
  const candidates = [path.parse(process.cwd()).root];
  if (process.env["SystemDrive"] !== undefined) {
    candidates.push(`${process.env["SystemDrive"]}\\`);
  }
  candidates.push("/");
  for (const candidate of candidates) {
    if (existsSync(candidate) && !hasStackMarkerAbove(candidate)) {
      return candidate;
    }
  }
  throw new Error(
    "no directory outside any repository could be found: every drive root on this " +
      "machine holds a docker-compose.yml at or above it, so a development launch " +
      "cannot be observed refusing to start.",
  );
}

test.describe("where a packaged app finds the stack", () => {
  test("reads the tree the installer shipped, not the working directory", () => {
    const resources = scratch();
    const stackRoot = path.join(resources, "stack");
    mkdirSync(stackRoot);
    writeFileSync(path.join(stackRoot, STACK_MARKER), "name: opspilot\n", "utf8");
    // A `docker-compose.yml` one level up is what makes this test distinguish
    // the two answers rather than merely pass either way: the walk-up search
    // would find this one, and the packaged path must not.
    writeFileSync(path.join(resources, STACK_MARKER), "name: wrong\n", "utf8");

    const resolved = resolveStackRoot({ packaged: true, resourcesPath: resources });

    expect(resolved).toBe(stackRoot);
    expect(readFileSync(path.join(resolved, STACK_MARKER), "utf8")).toBe("name: opspilot\n");
  });

  test("refuses an install whose stack directory is missing, and names it", () => {
    const resources = scratch();

    // The failure this replaces was a Compose error about a working directory
    // the app had invented, minutes after a window that said nothing. Asserting
    // the message contains the path is what keeps the message useful.
    expect(() => resolveStackRoot({ packaged: true, resourcesPath: resources })).toThrow(/stack/);
    expect(() => resolveStackRoot({ packaged: true, resourcesPath: resources })).toThrow(resources);
  });

  test("refuses a packaged build that was given no resources directory at all", () => {
    expect(() => resolveStackRoot({ packaged: true, resourcesPath: undefined })).toThrow(/resources/i);
  });

  test("walks up from the working directory when this is a development launch", () => {
    const stackRoot = fakeStackRoot();
    const nested = path.join(stackRoot, "web", ".next");
    mkdirSync(nested, { recursive: true });
    const before = process.cwd();
    try {
      process.chdir(nested);
      expect(resolveStackRoot({ packaged: false, resourcesPath: undefined })).toBe(stackRoot);
    } finally {
      process.chdir(before);
    }
  });

  test("still refuses a development launch started outside any repository", () => {
    // Kept because it is the development half of the same question, and a
    // future change that made `resolveStackRoot` always return something would
    // otherwise turn "no stack" into a Compose failure in a working directory
    // chosen by accident.
    //
    // **The directory has to be outside the repository, and that is the whole
    // difficulty.** `findRepoRoot` walks *up* from the working directory, so a
    // directory inside the checkout finds the repository's own
    // `docker-compose.yml` above it and the launch succeeds. This test used to
    // chdir into the project's scratch directory, and that directory used to be
    // the system temp directory — genuinely outside the repository, so the test
    // was proving what it said. When the scratch directory moved inside the
    // checkout, the test kept its name and its assertion and quietly stopped
    // being about that: a launch from "outside any repository" was being
    // observed from the middle of one, and the refusal it expected never came.
    const outside = directoryOutsideAnyRepository();
    const before = process.cwd();
    try {
      process.chdir(outside);
      expect(() => resolveStackRoot({ packaged: false, resourcesPath: undefined })).toThrow(STACK_MARKER);
    } finally {
      process.chdir(before);
    }
  });
});

test.describe("where a packaged app finds the operator token", () => {
  test("uses the app-data token when there is no repository to read", () => {
    const resolution = resolveOperatorToken({ fromAppDataEnv: "generated-on-this-machine" });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.token).toBe("generated-on-this-machine");
      expect(resolution.source).toBe("app data");
    }
  });

  test("still lets an operator's own source win over the generated one", () => {
    // The order is the decision this file exists for. Moving app data ahead of
    // these would silently make `export OPSPILOT_OPERATOR_TOKEN=…` do nothing,
    // which is the failure `token.spec.ts` was written to prevent.
    const resolution = resolveOperatorToken({
      fromEnvironment: "from-shell",
      fromAppDataEnv: "generated",
    });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.source).toBe("environment");
      expect(resolution.token).toBe("from-shell");
    }
  });

  test("treats a blank app-data value as absent, like every other source", () => {
    const resolution = resolveOperatorToken({ fromAppDataEnv: "   ", fromRepositoryEnv: "real-token" });

    expect(resolution.ok).toBe(true);
    if (resolution.ok) {
      expect(resolution.token).toBe("real-token");
    }
  });
});

test.describe("the app-data token file", () => {
  test("is created once and reused, because a per-launch token is a 401 on every approval", () => {
    const dir = scratch();

    const first = ensureOperatorToken(dir);
    const second = ensureOperatorToken(dir);

    expect(first).not.toBe("");
    // A different token on the next launch would leave the database holding
    // records written under the old one and refuse every new approval.
    expect(second).toBe(first);
  });

  test("is written in the same shape the app reads, under the one variable name", () => {
    const dir = scratch();
    const token = ensureOperatorToken(dir);

    // Read back with the same reader `main.ts` uses for the other two sources.
    // A file this script wrote in a shape nothing reads would be a token that
    // looks configured and behaves as absent.
    expect(readEnvValue(path.join(dir, TOKEN_ENV_FILENAME), TOKEN_ENV_KEY)).toBe(token);
    expect(readFileSync(path.join(dir, TOKEN_ENV_FILENAME), "utf8")).toContain(`${TOKEN_ENV_KEY}=`);
  });

  test("keeps a value an operator put there by hand", () => {
    const dir = scratch();
    writeFileSync(path.join(dir, TOKEN_ENV_FILENAME), `${TOKEN_ENV_KEY}=chosen-by-hand\n`, "utf8");

    expect(ensureOperatorToken(dir)).toBe("chosen-by-hand");
  });

  test("generates a token of its own when there is no file to read", () => {
    // This is what the function does when it is asked to generate one.
    //
    // The behaviour the old name promised -- "leaves no file for a launch
    // that resolved a token some other way" -- is **not** this function's to
    // decide: `main.ts` only calls it for a packaged launch, and only as the
    // last source, so an operator who exported a token never causes a file
    // to be written at all. That condition lives in `main.ts`, and this
    // package's specs cannot reach it -- so the name said a thing nothing
    // here asserted, and a reader would have believed it was covered.
    const dir = scratch();
    expect(ensureOperatorToken(dir)).toMatch(/^[0-9a-f]{64}$/);
    rmSync(dir, { recursive: true, force: true });
  });
});