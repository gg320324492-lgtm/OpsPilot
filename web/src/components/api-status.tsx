import { checkApiReachability } from "@/lib/api/client";

/**
 * The dashboard's connection banner.
 *
 * Its whole job is the failure case. A missing or wrong operator token makes
 * every request fail, and a dashboard that renders an empty list in that state
 * is asserting something false and entirely plausible -- "no tickets yet" -- so
 * an operator would have no reason to look for a configuration problem. A
 * console error is not a user interface.
 *
 * So the token is checked once on load, server-side, and the outcome is stated
 * in words on the page. Three states, deliberately distinct:
 *
 * - unreachable: the API is not answering at all (not running, wrong port).
 * - unauthenticated: the API answered and refused the token.
 * - ready: render nothing, so the shell stays quiet when things work.
 *
 * Reads the token server-side (see `client.ts`); the token never reaches here.
 */
export async function ApiStatus() {
  const status = await checkApiReachability();

  if (status.reachable && status.authenticated) {
    return null;
  }

  const isAuthProblem = status.reachable && !status.authenticated;

  return (
    <div
      role="status"
      className={
        isAuthProblem
          ? "border-b border-amber-300 bg-amber-50 px-6 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
          : "border-b border-red-300 bg-red-50 px-6 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
      }
    >
      <div className="mx-auto max-w-7xl">
        <strong>{isAuthProblem ? "API rejected the operator token" : "Cannot reach the API"}</strong>
        <span className="ml-2">{status.message}</span>
        {isAuthProblem ? (
          <p className="mt-1 text-xs opacity-80">
            The dashboard reads <code>OPSPILOT_OPERATOR_TOKEN</code> on the server and
            never sends it to the browser. Set it in <code>web/.env.local</code> to the
            same value the API process was started with, then restart the dev server.
          </p>
        ) : null}
      </div>
    </div>
  );
}