/**
 * Where the operator token comes from, and what happens when it is nowhere.
 *
 * ## Why the shell has to resolve this at all
 *
 * `docker-compose.yml` sets `OPSPILOT_OPERATOR_TOKEN: ${OPSPILOT_OPERATOR_TOKEN:?…}`
 * on the migrate, api and worker services: with the variable unset, Compose
 * refuses to render the file at all, so *every* Compose command in this app
 * fails before it starts. Compose satisfies it from the repository's own `.env`,
 * which it reads from its working directory — so a developer who has run the
 * stack by hand is fine, and a double-clicked installer on a fresh machine is
 * not.
 *
 * The dashboard needs it separately, for a different reason. `next build` with
 * `output: "standalone"` emits a `server.js` that reads `process.env` and
 * nothing else — no `.env` file is loaded at runtime — and the Route Handlers
 * under `web/src/app/api/` read `OPSPILOT_OPERATOR_TOKEN` from that environment.
 * A dashboard started without it renders perfectly and fails every privileged
 * write, which is the worst shape a failure can take here: a 200 page that
 * looks healthy.
 *
 * The last paragraph is the reason there is a fourth source. A packaged app has
 * no `.env` in a checkout and no `web/.env.local`, because it is not a
 * checkout — so on a clean machine the three sources here are all absent, and
 * `runtime/operator-token.ts` supplies a value this app generated for itself.
 * It is consulted last precisely because it is the one source the app owns
 * rather than the operator's.
 *
 * ## Why this is a function and not a file read in `main.ts`
 *
 * Because the *order* is the decision, and the order is arguable. An operator
 * launching from a shell that has already exported the token must win over the
 * repository's `.env`, or `export OPSPILOT_OPERATOR_TOKEN=…` would silently do
 * nothing. That is worth a named rule with a test rather than three `??`
 * operators in a file whose reader cannot see which one was chosen.
 */

import type { UserFacing } from "./types";

/** The candidate values, in the order they are consulted. */
export interface TokenSources {
  /** `process.env` of the shell process, then of the Electron main process. */
  readonly fromEnvironment?: string | undefined;
  /** The repository's `.env`, which Compose itself reads from the project dir. */
  readonly fromRepositoryEnv?: string | undefined;
  /** `web/.env.local`, which `npm run dev` reads and the standalone server does not. */
  readonly fromWebEnvLocal?: string | undefined;
  /**
   * `opspilot.env` in the app's user-data directory, which
   * `runtime/operator-token.ts` creates on the first packaged launch.
   *
   * Last because it is the only source this app owns rather than the
   * operator's, and the two sources above are ways an operator overrides it.
   */
  readonly fromAppDataEnv?: string | undefined;
}

export type TokenResolution =
  | { readonly ok: true; readonly token: string; readonly source: TokenSource; readonly divergence?: TokenSource[] }
  | { readonly ok: false; readonly problem: UserFacing };

export type TokenSource = "environment" | "repository .env" | "web/.env.local" | "app data";

const SOURCE_ORDER: readonly TokenSource[] = [
  "environment",
  "repository .env",
  "web/.env.local",
  "app data",
];

function valueOf(sources: TokenSources, source: TokenSource): string | undefined {
  const raw =
    source === "environment"
      ? sources.fromEnvironment
      : source === "repository .env"
        ? sources.fromRepositoryEnv
        : source === "web/.env.local"
          ? sources.fromWebEnvLocal
          : sources.fromAppDataEnv;
  const trimmed = raw?.trim();
  return trimmed === undefined || trimmed === "" ? undefined : trimmed;
}

/**
 * Pick the token, and say so when the sources disagree.
 *
 * A blank value counts as absent — `OPSPILOT_OPERATOR_TOKEN=` in a `.env` is a
 * documented placeholder (`web/.env.example` ships exactly that), and treating
 * it as a value would hand the dashboard an empty credential and produce a 401
 * on every write instead of the instruction below.
 *
 * The divergence report is not decoration. Two sources holding *different*
 * tokens is a configuration where the stack starts with one and the dashboard
 * proxies with the other, so every approve button 401s while the dashboard
 * still renders. Nothing about that failure names its cause; this line does.
 */
export function resolveOperatorToken(sources: TokenSources): TokenResolution {
  const found = SOURCE_ORDER.map((source) => ({ source, value: valueOf(sources, source) })).filter(
    (entry): entry is { source: TokenSource; value: string } => entry.value !== undefined,
  );

  const first = found[0];
  if (first === undefined) {
    return {
      ok: false,
      problem: {
        title: "No operator token is configured",
        detail:
          "OPSPILOT_OPERATOR_TOKEN is unset in this app's environment, in the repository's .env, " +
          "in web/.env.local, and in the app's own data file. docker-compose.yml sets it with " +
          "the `:?` form on the migrate, api and worker services, so Compose refuses to render " +
          "the project at all without it, and the dashboard's Route Handlers read it to approve " +
          "refunds on the browser's behalf.",
        remedy:
          "An installed copy writes this file itself on first launch, so reaching this message " +
          "means the app could not write to its data folder. If you are running from a checkout " +
          "instead, create web/.env.local from web/.env.example and give OPSPILOT_OPERATOR_TOKEN " +
          "a value of your own — it is a local-only credential for a local-only API, but it " +
          "should not be the example's. Then start the app again.",
      },
    };
  }

  const disagreeing = found.filter((entry) => entry.value !== first.value).map((entry) => entry.source);
  return {
    ok: true,
    token: first.value,
    source: first.source,
    ...(disagreeing.length === 0 ? {} : { divergence: disagreeing }),
  };
}