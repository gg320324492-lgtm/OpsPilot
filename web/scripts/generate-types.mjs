/**
 * Regenerate `src/lib/api/types.ts` from the *live* OpenAPI schema.
 *
 * Why a generator rather than hand-written interfaces: a hand-written response
 * type is a second copy of the backend's Pydantic models, and the two copies
 * disagree the first time someone adds a field. That failure mode -- a value
 * copied where it should be referenced -- is the one this project has been
 * documenting throughout, so the client is wired to the schema instead of
 * transcribed from it.
 *
 * The chain is:
 *
 *   create_app().openapi()            (scripts/openapi_schema.py, no DB)
 *        |  OpenAPI 3.1 JSON on stdout
 *        v
 *   openapi-typescript                (this script's dependency)
 *        |
 *        v
 *   src/lib/api/types.ts              (committed, and checked by --check)
 *
 * Run:  npm run generate:types
 * Verify:  npm run check:types   (the drift guard; exits non-zero on drift)
 *
 * The schema is produced by importing the FastAPI app in-process rather than by
 * fetching `http://localhost:8000/openapi.json`, so the types can be regenerated
 * and drift-checked without the API running and without a database. Both routes
 * read the same code; this one just does not need a port.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = resolve(WEB_ROOT, "..");
const EMITTER = join(REPO_ROOT, "scripts", "openapi_schema.py");
const TYPES_FILE = join(WEB_ROOT, "src", "lib", "api", "types.ts");

/** The project venv's interpreter. Falls back to `python` on PATH. */
const PYTHON = join(REPO_ROOT, ".venv", "Scripts", "python.exe");

const HEADER = `/**
 * API types, GENERATED -- do not edit.
 *
 * Produced by \`npm run generate:types\`, which builds the FastAPI app in
 * process (no database, no running server) and feeds its OpenAPI document to
 * \`openapi-typescript\`. The source of truth is
 * \`src/opspilot/api/schemas.py\`; this file is a projection of it.
 *
 * Editing this file by hand is undone by the next regeneration, and
 * \`npm run check:types\` fails the moment the committed copy no longer matches
 * the live schema -- so a hand-edit cannot survive as a silent divergence.
 *
 * Regenerate:  npm run generate:types
 * Verify:      npm run check:types
 */
`;

/**
 * Run the Python emitter and return the OpenAPI document as a JSON string.
 *
 * Uses `execFileSync` with the interpreter as argv[0] and no shell, so a path
 * containing spaces cannot be reinterpreted as shell syntax.
 *
 * @returns {string} The schema, as JSON.
 */
function emitSchema() {
  const command = process.env.OPSPILOT_PYTHON ?? PYTHON;
  try {
    return execFileSync(command, [EMITTER], {
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (cause) {
    process.stderr.write(
      `Could not emit the OpenAPI schema with ${command}.\n` +
        "Run this from a checkout where the project venv exists, or set\n" +
        "OPSPILOT_PYTHON to an interpreter that can import opspilot.\n\n" +
        String(cause.stderr ?? cause.message),
    );
    process.exit(1);
  }
}

/**
 * Convert the schema into TypeScript with openapi-typescript.
 *
 * The generator is loaded by import rather than shelled out so that the same
 * code path serves both `generate:types` and `check:types`; a guard that ran a
 * different command than the generator could disagree with it for reasons that
 * have nothing to do with drift.
 *
 * v7's programmatic entry point returns a TypeScript AST, not a string, so the
 * AST is serialised with the package's own `astToString`. That matters for the
 * guard: using the library's serialiser rather than hand-rolled string
 * concatenation means the committed file is byte-identical to what a future
 * `npx openapi-typescript` run would produce, so drift means drift.
 *
 * @param {string} schema The OpenAPI document as JSON.
 * @returns {Promise<string>} The generated TypeScript source.
 */
async function generateTypes(schema) {
  const { default: openapiTS, astToString } = await import("openapi-typescript");
  const ast = await openapiTS(JSON.parse(schema));
  const body = HEADER + "\n" + astToString(ast);
  return body.endsWith("\n") ? body : body + "\n";
}

/** Write the generated types, creating the directory if needed. */
function writeTypes(source) {
  mkdirSync(dirname(TYPES_FILE), { recursive: true });
  writeFileSync(TYPES_FILE, source, "utf8");
}

/**
 * Compare generated types against the committed file.
 *
 * Compares the *generated* text against the committed text byte for byte
 * rather than comparing schema hashes, because a hash tells you the files
 * differ but not how; a text diff points at the renamed field. `newlines`
 * normalisation exists only because Windows checkouts flip CRLF and that must
 * not read as drift.
 *
 * @param {string} generated The freshly generated source.
 * @returns {boolean} True when the committed file is already current.
 */
function isCurrent(generated) {
  let committed;
  try {
    committed = readFileSync(TYPES_FILE, "utf8");
  } catch {
    return false;
  }
  return normalise(committed) === normalise(generated);
}

/** Strip CRLF differences, which are a checkout artefact rather than drift. */
function normalise(text) {
  return text.replace(/\r\n/g, "\n");
}

async function main() {
  const check = process.argv.includes("--check");

  const generated = await generateTypes(emitSchema());

  if (check) {
    if (isCurrent(generated)) {
      process.stdout.write("client types are current with the live OpenAPI schema\n");
      return 0;
    }
    // Deliberately does NOT write the regenerated file. A check that repairs
    // the tree it is checking is not a check: it would make the failure
    // invisible in CI (the working tree comes back clean), and it would destroy
    // the only record of what drifted. `--check` reports and stops; the
    // developer runs `generate:types` and looks at the diff themselves.
    writeDiff(generated);
    process.stderr.write(
      "web/src/lib/api/types.ts does not match the live OpenAPI schema.\n" +
        "The backend changed and the client types were not regenerated -- which\n" +
        "is exactly the copy-diverging-from-source failure this guard exists for.\n\n" +
        "Run:  npm run generate:types\n" +
        "Then commit the result together with the backend change.\n",
    );
    return 1;
  }

  writeTypes(generated);
  process.stdout.write(`wrote ${TYPES_FILE}\n`);
  return 0;
}

/**
 * Print the difference between the committed types and what the live schema
 * produces, so the failure names the drifted field instead of only stating that
 * something differs.
 *
 * Delegates to `git diff --no-index` when git is available, because writing a
 * correct unified diff by hand is not the job. If git is missing, the message
 * degrades to a pointer at `generate:types` rather than failing confusingly --
 * a check that cannot describe its own failure should still report the failure.
 */
function writeDiff(generated) {
  const scratch = mkdtempSync(join(tmpdir(), "opspilot-types-"));
  const candidate = join(scratch, "types.ts");
  try {
    writeFileSync(candidate, generated, "utf8");
    const diff = execFileSync("git", ["diff", "--no-index", "--", TYPES_FILE, candidate], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    process.stderr.write(diff);
  } catch (cause) {
    // `git diff --no-index` exits 1 when files differ -- which is the normal
    // case here, and carries the diff on stdout rather than in `cause`. Any
    // other failure means git is unavailable or refused; stay quiet rather than
    // pretending, since the caller already has the instructions.
    if (cause && typeof cause === "object" && "stdout" in cause) {
      process.stderr.write(String(cause.stdout ?? ""));
    }
  } finally {
    // Best-effort: a leftover temp directory is not worth failing a check over.
    rmSync(scratch, { recursive: true, force: true });
  }
}

// `main` resolves to the exit code, which has to be handed to the process or a
// drifting check exits 0 while printing that it failed -- a guard that reports
// failure and passes anyway teaches the team to ignore it.
process.exitCode = await main();