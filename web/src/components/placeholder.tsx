/**
 * A placeholder for a screen that has not been built yet.
 *
 * Each of the six screens is separate work, so the routes exist and render their
 * own name. A stub that says "Tickets — not yet implemented" is honest about
 * what is missing; a blank page or a `Coming soon` with no anchor would read as
 * a bug in a working dashboard, and would be indistinguishable from one.
 */
export function Placeholder({ title }: { title: string }) {
  return (
    <div className="mx-auto max-w-7xl px-6 py-10">
      <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 text-sm text-neutral-500 dark:text-neutral-400">
        This screen is not built yet. The API client it will use is in place and
        type-checked against the live schema.
      </p>
    </div>
  );
}