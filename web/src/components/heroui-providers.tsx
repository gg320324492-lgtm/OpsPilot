"use client";

/**
 * The client-side providers HeroUI's interactive components need.
 *
 * HeroUI v3 needs no global provider for rendering; `I18nProvider` matters for
 * the components that read locale-aware ARIA and date formatting (Select,
 * Dialog, Table), and `ToastProvider` hosts the toast queue. Both are client
 * components by nature, so they live in this one small `"use client"` island
 * while the rest of the layout stays server-rendered.
 *
 * `locale` is resolved on the server (`layout.tsx` already reads it) and passed
 * down, so the client never has to guess it and there is no first-paint
 * locale flicker.
 *
 * ## WHY `children` SITS OUTSIDE `<ToastProvider>` — DO NOT NEST IT BACK
 *
 * HeroUI's `ToastProvider` renders react-aria's `ToastRegion`, which returns
 * `createPortal(...)` only when the queue is non-empty and returns `null`
 * otherwise -- so with no toast ever fired, the provider renders `null` during
 * SSR. Wrapping `children` inside it makes the ENTIRE server-rendered page
 * `null`: the HTML body arrives as an empty React placeholder (`<!--$--><!--/$-->`)
 * with the page tree confined to the RSC flight payload. React never hydrates
 * that shell (the client runtime errors out before mounting), so every
 * Playwright spec -- which waits for real buttons via `getByRole` -- hangs
 * until its timeout. That is the 25-minute-silent web-e2e job of 2026-11-16.
 * Rendering the provider as a SIBLING keeps the server tree intact while the
 * toast portal still mounts client-side when a toast is actually raised.
 */

import type { ReactNode } from "react";
import { I18nProvider, ToastProvider } from "@heroui/react";

export function HeroUIProviders({ locale, children }: { locale: string; children: ReactNode }) {
  return (
    <I18nProvider locale={locale}>
      {children}
      <ToastProvider />
    </I18nProvider>
  );
}
