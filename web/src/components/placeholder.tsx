/**
 * A placeholder for a screen that has not been built yet.
 *
 * Each of the six screens is separate work, so the routes exist and render their
 * own name. A stub that says "Tickets — not yet implemented" is honest about
 * what is missing; a blank page or a `Coming soon` with no anchor would read as
 * a bug in a working dashboard, and would be indistinguishable from one.
 *
 * ## ITS COPY IS NOT IN THE CATALOGUE, AND THAT IS A KNOWN GAP
 *
 * Every other user-visible string in `src/` resolves through
 * `src/i18n/dictionaries/`. The sentence below does not, and `title` arrives as
 * a bare `string` rather than through `getT()`. Nothing imports this component
 * -- all six screens are built -- so localising it would add catalogue entries
 * for a page nobody can reach, which is worse than the gap. Anyone who wires a
 * seventh screen back up must move both of these into the catalogue first; the
 * parity test in `e2e/i18n-catalogue.spec.ts` will not catch it, because this
 * component does not call `t`.
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