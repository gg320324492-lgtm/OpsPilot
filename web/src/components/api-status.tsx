import { checkApiReachability } from "@/lib/api/client";
import { getT } from "@/i18n/server";

/**
 * The banner across the top of every screen when the API is not usable.
 *
 * A Server Component, so the two sentences that name the failure are resolved
 * from the operator's locale before the first byte is written. That matters more
 * here than on most screens: this is the one message an operator sees when the
 * dashboard has nothing else to show them, and it is the one that tells them
 * which environment variable to set.
 *
 * `status.message` is the client's own sentence about the transport failure and
 * is passed through as it was, for the reason `ErrorPanel`'s docstring gives.
 */
export async function ApiStatus() {
  const [status, t] = await Promise.all([checkApiReachability(), getT()]);

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
        <strong>
          {isAuthProblem ? t("apiStatus.tokenRejected") : t("apiStatus.unreachable")}
        </strong>
        <span className="ml-2">{status.message}</span>
        {isAuthProblem ? (
          <p className="mt-1 text-xs opacity-80">
            {t("apiStatus.tokenHelp_before")} <code>{t("common.envOperatorToken")}</code>{" "}
            {t("apiStatus.tokenHelp_middle")} <code>{t("apiStatus.envFile")}</code>{" "}
            {t("apiStatus.tokenHelp_after")}
          </p>
        ) : null}
      </div>
    </div>
  );
}