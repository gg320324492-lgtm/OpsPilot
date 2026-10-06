import Link from "next/link";

/**
 * The dashboard's navigation shell.
 *
 * The five top-level screens land in these routes; Run Detail is reached from a
 * run id rather than from here. Every route is reachable from any other, which
 * matters for the two things an operator has to be able to do without hunting:
 * see whether anything is parked, and see whether the knowledge index is empty.
 */
const ROUTES = [
  { href: "/", label: "Dashboard" },
  { href: "/tickets", label: "Tickets" },
  { href: "/runs", label: "Runs" },
  { href: "/approvals", label: "Approvals" },
  { href: "/knowledge", label: "Knowledge" },
] as const;

export function Nav() {
  return (
    <nav className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950">
      <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
        <span className="text-sm font-semibold tracking-tight">OpsPilot</span>
        <ul className="flex items-center gap-4 text-sm">
          {ROUTES.map((route) => (
            <li key={route.href}>
              <Link
                href={route.href}
                className="text-neutral-600 transition-colors hover:text-neutral-950 dark:text-neutral-400 dark:hover:text-neutral-50"
              >
                {route.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}