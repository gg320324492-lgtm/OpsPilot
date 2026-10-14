/**
 * D3's acceptance, against the artefact the installer actually ships.
 *
 * ## The gap this closes
 *
 * `docs/phase2-plan.md` §P2-D3 reads: *"Every control in P2-A must hold in the
 * packaged app: loopback binding, origin checks, no token in the renderer.
 * **Acceptance:** the packaged app is checked against A1–A3 the same way the dev
 * stack is."*
 *
 * Every other spec in this directory checks something *adjacent* to that:
 *
 *   * `security.spec.ts` asserts `SECURE_WEB_PREFERENCES` and the navigation
 *     allowlist as **source constants** — what the app intends.
 *   * `shell.spec.ts` launches Electron with `args: [DESKTOP_DIR]`, the
 *     development tree, so `app.isPackaged` is `false` in every automated run.
 *     It proves a *dev* app keeps its renderer untrusted.
 *   * `packaging.spec.ts` tests `resolveStackRoot`/`resolveOperatorToken` as
 *     pure functions against a scratch directory.
 *
 * So nothing ran the packaged branch of `main.ts`, and nothing inspected what
 * the installer contains. The only check of the packaged artefact anywhere was
 * `docs/installer-verification.md` §5 — a list of `netstat`, `curl` and `dir`
 * commands for a human. That is a real check and it is why D2 is a manual step,
 * but D3 does not say *a* check exists; it says the packaged app is checked the
 * same way the dev stack is.
 *
 * ## What this asserts, and what it deliberately does not
 *
 * This checks the **artefact**, not a running instance of it. Two reasons:
 *
 *   1. Some of A1–A3 are properties of what was *shipped*, and a running app is
 *      the wrong instrument for them. "The installer contains no credential" is
 *      a statement about the files in the package; asking an installed copy is
 *      asking after the fact.
 *   2. Launching the packaged app runs the real stack — it spawns Docker
 *      Compose, opens a window, and writes an operator token into `%APPDATA%`.
 *      A test that does that on every push is a test that mutates the machine it
 *      runs on, which is what `installer-verification.md` exists to do
 *      deliberately and once.
 *
 * **It does not run `OpsPilot.exe`, and it does not claim to.** The live check
 * against an installed copy stays a documented manual procedure, and
 * `docs/limitations.md` §8 records that boundary rather than this file hiding
 * it. What changes is that "the packaged app was checked against A1–A3" is no
 * longer *only* a manual claim: the half that can be asserted from the artefact
 * is asserted here, on every push.
 *
 * ## Why it skips rather than fails when there is no package
 *
 * `desktop/release/` is gitignored (`desktop/.gitignore:13`) and built by
 * `npm run package`, so a fresh clone and CI have no artefact to read. That is
 * the ADR-0004 pattern the rest of this suite uses for Docker and for a display:
 * skip, naming what is missing. A machine that *has* the package and whose
 * package fails these rules is a failure, never a skip — that is this file's
 * whole job.
 */

import { expect, test } from "@playwright/test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

const DESKTOP_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_DIR, "..");

/** Where `electron-builder` writes the unpacked tree, and the app inside it. */
const UNPACKED_DIR = path.join(DESKTOP_DIR, "release", "win-unpacked");
const PACKAGED_STACK = path.join(UNPACKED_DIR, "resources", "stack");

/**
 * The installed tree, or `null` with a recorded reason.
 *
 * The suite runs on Windows, macOS and Linux in different contexts; the package
 * is currently produced for Windows only. Both the platform and the missing
 * artefact skip rather than fail, because neither is a defect in the app.
 */
function packagedStackOrSkip(): string | null {
  if (process.platform !== "win32") {
    test.skip(true, "the packaged artefact is produced for Windows only");
    return null;
  }
  if (!existsSync(PACKAGED_STACK)) {
    test.skip(
      true,
      `no packaged artefact to inspect at ${PACKAGED_STACK}; run \`npm run package\` in ` +
        "desktop/ to build one. Its absence is why this check is a skip and not a " +
        "failure -- release/ is gitignored and reproducible (desktop/.gitignore).",
    );
    return null;
  }
  return PACKAGED_STACK;
}

/** Every file under `dir`, as paths relative to it. */
function walk(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...walk(full).map((p) => path.join(entry.name, p)));
    } else {
      found.push(entry.name);
    }
  }
  return found;
}

/** The published port bindings in a compose file, as written. */
function publishedPorts(composeText: string): string[] {
  return [...composeText.matchAll(/^\s*-\s*"([^"]*:\d+)"\s*$/gm)].map((m) => m[1]);
}

test.describe("A1: the shipped stack binds to loopback", () => {
  test("every published port in the packaged compose file names 127.0.0.1", () => {
    const stack = packagedStackOrSkip();
    if (stack === null) {
      return;
    }

    const compose = readFileSync(path.join(stack, "docker-compose.yml"), "utf8");
    const ports = publishedPorts(compose);

    // A compose file with no published ports would pass the loop below
    // vacuously. The stack has three, and this asserts the check found them.
    expect(ports.length).toBeGreaterThanOrEqual(3);

    for (const binding of ports) {
      expect(binding.startsWith("127.0.0.1:")).toBe(true);
    }
  });

  test("the packaged compose file is the repository's, not a stale copy", () => {
    const stack = packagedStackOrSkip();
    if (stack === null) {
      return;
    }

    // `stage-stack.mjs` copies the checkout into `resources/stack`. If a stale
    // copy were ever shipped, the port assertion above would be checking the
    // wrong file and reporting green -- so the two are compared, and a package
    // built from a tree whose compose has since changed fails here by design.
    const packaged = readFileSync(path.join(stack, "docker-compose.yml"), "utf8");
    const repository = readFileSync(path.join(REPO_ROOT, "docker-compose.yml"), "utf8");
    expect(packaged).toBe(repository);
  });
});

test.describe("A3: the installer ships no credential", () => {
  test("no .env or operator token file is inside the package", () => {
    const stack = packagedStackOrSkip();
    if (stack === null) {
      return;
    }

    const files = walk(stack);
    const credentials = files.filter((name) => {
      const base = path.basename(name);
      return base === ".env" || (base.startsWith(".env.") && base !== ".env.example");
    });

    expect(credentials).toEqual([]);
  });

  test("the token the installer would otherwise embed appears in no packaged file", () => {
    const stack = packagedStackOrSkip();
    if (stack === null) {
      return;
    }

    // The repository's own `.env` is gitignored but present on a developer's
    // machine, and `stage-stack.mjs` runs on that machine. This is the assertion
    // that a build from a working checkout did not sweep it up: if the token is
    // in the environment or the repo `.env`, it must not be in the package.
    const token = process.env.OPSPILOT_OPERATOR_TOKEN;
    const repoEnv = path.join(REPO_ROOT, ".env");
    const expected = token ?? (existsSync(repoEnv) ? readEnvToken(repoEnv) : undefined);
    if (expected === undefined || expected === "") {
      test.skip(true, "no operator token in the environment or the repository .env to look for");
      return;
    }

    const hits: string[] = [];
    for (const relative of walk(stack)) {
      const full = path.join(stack, relative);
      // Only text files are read; a binary match would be a coincidence, not a
      // leak, and reading 100 MB of images to look for a string is not a check.
      if (statSync(full).size > 2_000_000) {
        continue;
      }
      let content: string;
      try {
        content = readFileSync(full, "utf8");
      } catch {
        continue;
      }
      if (content.includes(expected)) {
        hits.push(relative);
      }
    }

    expect(hits).toEqual([]);
  });
});

test.describe("D3: the packaged shell keeps the renderer untrusted", () => {
  test("the security policy inside app.asar is the source policy", () => {
    const stack = packagedStackOrSkip();
    if (stack === null) {
      return;
    }
    const asar = path.join(UNPACKED_DIR, "resources", "app.asar");
    if (!existsSync(asar)) {
      test.skip(true, "app.asar is absent; the unpacked tree was built without it");
      return;
    }

    // `asar` is a simple archive: a fixed 16-byte preamble, then a JSON header,
    // then the file contents. The preamble is four little-endian uint32s --
    // pickle size, header-size pickle, header size, header string size -- and it
    // is the **fourth**, not the third, that bounds the JSON. Reading the third
    // parses a header that stops short and reports every entry as missing.
    const buffer = readFileSync(asar);
    const headerStringSize = buffer.readUInt32LE(12);
    const header = JSON.parse(buffer.subarray(16, 16 + headerStringSize).toString("utf8"));

    /**
     * The offset and size of a packaged file, from the asar header.
     *
     * The header nests every directory under a `files` key -- `{"files": {"dist":
     * {"files": {"security-policy.js": {...}}}}}` -- so the walk descends through
     * `files` at each level rather than treating the directory name as the key
     * holding the next map. Getting that wrong reports every entry as absent,
     * which is indistinguishable from a package that genuinely lacks the file.
     */
    function findEntry(node: unknown, target: string[]): { offset: string; size: number } | null {
      if (typeof node !== "object" || node === null || target.length === 0) {
        return null;
      }
      const files = (node as { files?: Record<string, unknown> }).files;
      if (files === undefined) {
        return null;
      }
      const next = files[target[0]];
      if (next === undefined) {
        return null;
      }
      if (target.length === 1) {
        return "offset" in (next as object) ? (next as { offset: string; size: number }) : null;
      }
      return findEntry(next, target.slice(1));
    }

    const entry = findEntry(header, ["dist", "security-policy.js"]);
    expect(entry).not.toBeNull();
    if (entry === null) {
      return;
    }

    // The asar's contents begin after the header, padded to a 4-byte boundary.
    const contentStart = 16 + headerStringSize;
    const embedded = buffer
      .subarray(contentStart + Number(entry.offset), contentStart + Number(entry.offset) + entry.size)
      .toString("utf8");

    // The three settings A2/D3 turn on, read out of the shipped bundle rather
    // than the source. If electron-builder ever shipped a stale or transformed
    // copy, the source assertions in security.spec.ts would still pass.
    expect(embedded).toContain("contextIsolation: true");
    expect(embedded).toContain("nodeIntegration: false");
    expect(embedded).toContain("sandbox: true");
  });
});

/** The value of a key in a dotenv-shaped file, or `""`. */
function readEnvToken(file: string): string {
  const match = readFileSync(file, "utf8").match(/^OPSPILOT_OPERATOR_TOKEN=(.*)$/m);
  return match === null ? "" : match[1].trim();
}
