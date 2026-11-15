/**
 * The API's host port: one variable, two halves, and the walkthrough that found
 * them disagreeing.
 *
 * ## The failure this spec is about
 *
 * `docker-compose.yml` has published the API on
 * `127.0.0.1:${OPSPILOT_API_HOST_PORT:-8000}:8000` since the ports became
 * configurable, but the shell probed a hard-coded `http://127.0.0.1:8000`. On a
 * machine where 8000 is already taken — the independent 2026-11-15 walkthrough
 * of the v0.2.0 candidate ran on exactly such a machine — the stack could be
 * moved, healthy and answering, while the shell polled an address nothing
 * answered: `/ready` never returned, and the dashboard step never completed.
 * The walker recorded it as the one substantive gap in an otherwise passing
 * walk.
 *
 * ## What is asserted, and why each claim earns its place
 *
 *   1. The variable is parsed strictly. `Number("3000abc")` is `NaN` and
 *      `Number("")` is `0`; both would otherwise reach a URL that silently
 *      probes the wrong port. A mistyped value must fail at start-up naming the
 *      variable, not surface as a probe that never succeeds.
 *   2. The URL is loopback, because every published port in this repository
 *      binds `127.0.0.1` and a desktop build must not widen that (P2-A1/D3).
 *   3. **The two halves agree.** The default here is compared against the
 *      default literal in `docker-compose.yml`, read from the real file. Two
 *      hard-coded 8000s that merely happen to match today is the same defect
 *      one edit away; this makes the compose line the single source of the
 *      number and fails the day the two drift.
 */

import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  API_HOST_PORT_ENV_KEY,
  DEFAULT_API_PORT,
  DEFAULT_API_URL,
  resolveApiHostPort,
  resolveApiUrl,
} from "../src/runtime/dependencies";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const COMPOSE_FILE = path.join(REPO_ROOT, "docker-compose.yml");

test.describe("the API host port the shell probes", () => {
  test("defaults to 8000 when the variable is unset or blank", () => {
    expect(resolveApiHostPort(undefined)).toBe(DEFAULT_API_PORT);
    expect(resolveApiHostPort("")).toBe(DEFAULT_API_PORT);
    expect(resolveApiHostPort("   ")).toBe(DEFAULT_API_PORT);
  });

  test("accepts a port the stack was moved to, trimmed", () => {
    expect(resolveApiHostPort("18001")).toBe(18001);
    expect(resolveApiHostPort(" 18001 ")).toBe(18001);
    expect(resolveApiUrl("18001")).toBe("http://127.0.0.1:18001");
  });

  test("refuses a value that is not a TCP port, and names the variable", () => {
    for (const bad of ["3000abc", "0", "-1", "65536", "1.5", "NaN"]) {
      expect(() => resolveApiHostPort(bad), `"${bad}" should be refused`).toThrow(
        API_HOST_PORT_ENV_KEY,
      );
    }
  });

  test("builds the loopback URL the compose file actually publishes", () => {
    // The published address is loopback for the same reason the dashboard's is:
    // this app never widens the surface P2-A1 closed.
    expect(DEFAULT_API_URL).toBe("http://127.0.0.1:8000");
    expect(resolveApiUrl(undefined)).toBe(DEFAULT_API_URL);
  });

  test("shares its default with docker-compose.yml rather than duplicating it", () => {
    // `127.0.0.1:${OPSPILOT_API_HOST_PORT:-8000}:8000` is the one line that
    // decides what the stack publishes. If its default moves, this shell's must
    // move with it; a walker should not have to discover that again.
    const compose = readFileSync(COMPOSE_FILE, "utf8");
    const published = compose.match(/\$\{OPSPILOT_API_HOST_PORT:-(\d+)\}:8000/);
    expect(
      published,
      "docker-compose.yml no longer publishes the API with ${OPSPILOT_API_HOST_PORT:-<port>}:8000",
    ).not.toBeNull();
    expect(Number(published?.[1])).toBe(DEFAULT_API_PORT);
  });
});
