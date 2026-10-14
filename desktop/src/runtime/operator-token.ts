/**
 * The operator token for a machine that has no repository to read one from.
 *
 * ## The problem this solves
 *
 * `docker-compose.yml` sets `OPSPILOT_OPERATOR_TOKEN` with the `:?` form, and
 * Compose satisfies it from a `.env` beside the compose file — in the checkout.
 * A packaged app ships `docker-compose.yml` inside `resources/stack/` with no
 * `.env` next to it, because shipping one would put a credential in a file
 * everyone who downloads the installer has. So an installed app has exactly
 * three ways to get a token and none of them exist on a clean machine: the
 * environment (only if the operator set one by hand), the repository `.env`
 * (no repository), and `web/.env.local` (no repository).
 *
 * The fourth source is this module: `opspilot.env` in the app's user-data
 * directory, created on first run with a value generated on that machine.
 *
 * ## Why a generated token, and not one written into the installer
 *
 * A token baked into the artefact is a shared secret in every download, and this
 * one guards refunds. Generating it on the target means the installer contains
 * no credential at all, and two machines installed from the same file do not
 * share one. It is stable across launches because it is written to disk once
 * and read back after that, which matters: Compose and the dashboard's Route
 * Handlers must agree, and a token that changed per launch would 401 every
 * approval.
 *
 * It is not a defence against anyone with this Windows account — the file sits
 * in that account's own application-data folder. It is a loopback-only
 * credential for a loopback-only API, and it is not the control that keeps the
 * API off the network; `docker-compose.yml`'s loopback port bindings are.
 *
 * ## Why this file rather than a line in `main.ts`
 *
 * The *order* the sources are consulted in is `lifecycle/token.ts`'s and is
 * tested there. The reading and the writing are filesystem work, and
 * `runtime/dependencies.ts` owns the filesystem — this is the same split, so
 * this module takes a directory and returns a string and imports no Electron.
 */

import { randomBytes } from "node:crypto";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { readEnvValue } from "./dependencies";

/** The file, inside the app's user-data directory, that holds the token. */
export const TOKEN_ENV_FILENAME = "opspilot.env";

/** The one variable it holds. Named once so this file and `main.ts` cannot drift. */
export const TOKEN_ENV_KEY = "OPSPILOT_OPERATOR_TOKEN";

/**
 * Bytes of entropy in a generated token, rendered as hex.
 *
 * 32 bytes is the size of a full HMAC key and is not a judgement call worth
 * making per call site. The token is never shown to anyone, so nothing depends
 * on it being memorable.
 */
const TOKEN_BYTES = 32;

/**
 * Read the app-data token, creating one if the file does not have it yet.
 *
 * Idempotent by construction: the value is read back before anything is
 * written, so a second launch writes nothing and the dashboard and Compose keep
 * agreeing. `userDataDir` is `app.getPath("userData")`, created on demand
 * because a first launch is the one time it may not exist.
 *
 * @throws if the directory cannot be created or the file cannot be written.
 *   There is no fallback value: a token invented in memory and never persisted
 *   would be a different token on every launch, which is the failure this
 *   module exists to prevent.
 */
export function ensureOperatorToken(userDataDir: string): string {
  mkdirSync(userDataDir, { recursive: true });
  const file = path.join(userDataDir, TOKEN_ENV_FILENAME);

  const existing = readEnvValue(file, TOKEN_ENV_KEY);
  if (existing !== undefined && existing.trim() !== "") {
    return existing.trim();
  }

  const token = randomBytes(TOKEN_BYTES).toString("hex");
  writeFileSync(file, `${TOKEN_ENV_KEY}=${token}\n`, { encoding: "utf8" });
  // Owner-only on the platforms where a mode bit means one. On Windows Node
  // reports this as a success and maps it to almost nothing — the real control
  // there is the ACL on the user's application-data folder, which this app
  // inherits and does not widen. Written anyway so the Linux and macOS builds
  // of the same shell do not need a separate answer.
  try {
    chmodSync(file, 0o600);
  } catch {
    // A filesystem with no permission bits (a FAT32 volume, a mounted share)
    // refuses this. The file was written, and refusing to narrow a mode we
    // cannot set is not a reason to fail a launch.
  }
  return token;
}