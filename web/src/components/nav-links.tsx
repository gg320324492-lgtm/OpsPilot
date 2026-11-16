"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The nav link list, as a Client Component: current-page highlight needs the
 * live pathname, which only exists on the client. `usePathname` drives both
 * the visual highlight and `aria-current="page"`, so the state is announced
 * to assistive technology and not only painted. `labels` is resolved on the
 * server (the parent reads `getT()`), keeping the locale decision in one
 * place.
 */
export function NavLinks({
  labels,
}: {
  labels: Record<string, string>;
}) {
  const pathname = usePathname();

  return (
    <ul className="flex items-center gap-4 text-sm">
      {ROUTES.map((route) => {
        const isCurrent =
          route.href === "/" ? pathname === "/" : pathname.startsWith(route.href);
        return (
          <li key={route.href}>
            <Link
              href={route.href}
              aria-current={isCurrent ? "page" : undefined}
              className={
                isCurrent
                  ? "font-medium text-accent underline underline-offset-4"
                  : "text-muted transition-colors hover:text-foreground"
              }
            >
              {labels[route.key]}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

const ROUTES = [
  { href: "/", key: "dashboard" },
  { href: "/tickets", key: "tickets" },
  { href: "/runs", key: "runs" },
  { href: "/approvals", key: "approvals" },
  { href: "/knowledge", key: "knowledge" },
] as const;
