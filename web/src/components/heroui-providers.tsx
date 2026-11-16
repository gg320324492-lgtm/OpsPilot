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
 */

import type { ReactNode } from "react";
import { I18nProvider, ToastProvider } from "@heroui/react";

export function HeroUIProviders({ locale, children }: { locale: string; children: ReactNode }) {
  return (
    <I18nProvider locale={locale}>
      <ToastProvider>{children}</ToastProvider>
    </I18nProvider>
  );
}
