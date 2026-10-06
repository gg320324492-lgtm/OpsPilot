/**
 * Presentation helpers shared by the six screens.
 *
 * Kept in one module rather than inline in each screen for one reason: two
 * screens rendering the same field differently is how a reader concludes the
 * values disagree when they do not. Every function here is total -- it takes
 * what the API actually sends, including the `null`s the contract allows, and
 * returns something a reader can read.
 *
 * Deliberately *not* here: anything that decides what a value means. Status
 * colouring is a judgment, and it is stated once in `statusTone` below rather
 * than inferred at six call sites.
 */

/**
 * Parse a timestamp the API sent, as an absolute instant.
 *
 * **This exists because the API sends naive timestamps on SQLite.** The models
 * declare `DateTime(timezone=True)` and every value written is timezone-aware
 * UTC, which Postgres round-trips as `...+00:00`. SQLite has no native
 * timestamptz: it stores the string and drops the offset, so the same row
 * serialises as `"2026-10-06T07:49:41.816450"` -- no `Z`, no offset.
 *
 * That matters more than it looks. `new Date("2026-10-06T07:49:41.816450")` is
 * parsed by ECMAScript as **local** time, so on a machine at UTC+8 a run that
 * finished five minutes ago renders as "8h ago" and the dashboard opens with a
 * demonstrably false claim on it. Observed on this machine, from the real
 * golden path.
 *
 * So a value with no offset is read as UTC, which is what it is -- the storage
 * layer lost the marker, not the instant. A value that *does* carry an offset
 * (the Postgres deployment, where the round-trip preserves it) is parsed as
 * given, so both deployments render the same instant correctly.
 *
 * The alternative, "fix it in the backend", would mean changing a column type
 * or a serialiser for a driver-specific quirk in the local SQLite path, which
 * is the configuration used only by development and by the test suite
 * (ADR-0004). Normalising on read keeps the wire contract as specified and puts
 * the workaround where the quirk is.
 */
export function parseTimestamp(value: string): Date | null {
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value.trim());
  // A space-separated datetime (SQLAlchemy's default string form) is not valid
  // ISO 8601 and `new Date` returns Invalid Date for it in V8, so the
  // separator is normalised here too rather than in each caller.
  const iso = value.includes("T") ? value : value.replace(" ", "T");
  const parsed = new Date(hasOffset ? iso : `${iso}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Render a timestamp for an operator reading a wall clock.
 *
 * The API sends UTC (`docs/api-contract.md` §3 and `docs/data-model.md` §1).
 * Rendering the UTC string verbatim would put a run's created time next to a
 * reviewer's local time, and "the run finished at 07:31" would read as two
 * hours ago for anyone east of London. Converted to local here, once, and only
 * once -- every screen shares these functions so two screens cannot disagree
 * about when the same run happened.
 *
 * The date is included because a run list spans days: a bare clock time makes
 * yesterday's run look like it happened a minute ago.
 *
 * A value that will not parse returns the raw string rather than "Invalid Date".
 * The raw string is at least true, and this is a display path -- an unparseable
 * timestamp is a backend defect that should be visible, not smoothed over.
 */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return value;
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Just the clock, for a timeline whose day is already stated in the header. */
export function formatClock(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return value;
  }
  return parsed.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * How long ago something happened, in words.
 *
 * Used only where the absolute time is *also* on screen. A relative time alone
 * is the classic dashboard lie: "3 minutes ago" on a page that has been open
 * for ten is wrong and reads as live, so every use here sits beside
 * `formatTimestamp`'s output rather than replacing it.
 */
export function formatRelative(value: string | null | undefined, now = Date.now()): string {
  if (!value) {
    return "";
  }
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return "";
  }
  const seconds = Math.round((now - parsed.getTime()) / 1000);
  if (seconds < 5) {
    return "just now";
  }
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * Render a step's `latency_ms`.
 *
 * M7a measured `latency_ms` on every *work* step and deliberately left it NULL
 * on `state_change`, because between two run statuses there is no external work
 * and timing one measures the speed of a database UPDATE. So a null here is not
 * missing data and is not rendered as zero -- it is rendered as "not measured",
 * and the timeline says which kind of step it is looking at. A column that
 * showed `0` for fourteen rows would teach a reader that the column measures
 * something when it does not.
 *
 * Sub-millisecond values are real (the fake provider answers in well under a
 * millisecond), so `0` is a measurement and renders as `0 ms` rather than as a
 * dash.
 */
export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

/**
 * The colour class for a run status.
 *
 * One table, stated once. The three levels mean three different things to an
 * operator and the distinction is the point of the dashboard: `waiting_approval`
 * is *human action required*, `failed` is *the system did not do the job*, and
 * the in-flight states are *progress*. A screen that coloured all three amber
 * would be answering a question nobody asked.
 */
export function statusTone(status: string): string {
  switch (status) {
    case "completed":
      return "bg-emerald-100 text-emerald-900 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30";
    case "failed":
      return "bg-red-100 text-red-900 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30";
    case "waiting_approval":
      return "bg-amber-100 text-amber-900 ring-amber-600/30 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/40";
    case "received":
    case "classifying":
    case "retrieving":
    case "planning":
    case "executing":
    case "responding":
      return "bg-sky-100 text-sky-900 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-400/30";
    default:
      return "bg-neutral-100 text-neutral-800 ring-neutral-500/20 dark:bg-neutral-800 dark:text-neutral-200 dark:ring-neutral-400/30";
  }
}

/** `waiting_approval` -> `Waiting approval`. Never throws on an unknown status. */
export function humanise(value: string): string {
  if (!value) {
    return "—";
  }
  return value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** A UUID's first eight characters, which is how an operator refers to a run. */
export function shortId(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return value.slice(0, 8);
}