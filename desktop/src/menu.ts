/**
 * The native menu, and keeping it in the operator's language.
 *
 * ## THE PROBLEM THIS SOLVES
 *
 * The dashboard's language is an operator decision, stored in a cookie
 * (`opspilot_locale`, `web/src/i18n/config.ts`). The menu bar is drawn by the
 * OS, from the main process, which had no idea what that decision was. A
 * Chinese operator got a Chinese page and an English menu bar, which is the
 * kind of half-translated application that makes an operator doubt the screen
 * in front of them.
 *
 * ## WHY THE COOKIE, AND NOT AN IPC CHANNEL
 *
 * The obvious design is a new channel: the page tells the main process which
 * language it is in, and the main process rebuilds the menu. That needs a
 * preload change, a renderer change, a channel name, and a sender check — and
 * it still does not answer the question that matters most, which is *what
 * language was this application in when it started*.
 *
 * The cookie answers it, because the cookie is the decision itself, and the
 * main process can read it. `httpOnly` hides a cookie from script in the
 * page; it does not hide it from the session that stores it. Measured on the
 * pinned Electron (44.7.1) with a `Set-Cookie` from a real HTTP response:
 *
 *     session.defaultSession.cookies.get(...)  ->  the cookie, httpOnly: true
 *     document.cookie in the renderer           ->  ""
 *
 * So the main process reads the same cookie the dashboard renders from, and
 * the menu cannot disagree with the page. `e2e/menu-electron.spec.ts` drives
 * exactly that, in a real Electron.
 *
 * The cookie is set on the dashboard's origin, so it is read with the
 * dashboard's URL. Cookies are matched by host and path and not by port, so
 * the operator's choice survives the dashboard moving to another port — which
 * it does whenever `OPSPILOT_DESKTOP_PORT` is set.
 *
 * ## WHY A CHANGED EVENT AND NOT A NAVIGATION
 *
 * The language switcher is a link to `/api/locale?locale=zh-CN&returnTo=…`,
 * so the main process *could* watch `will-navigate` and read the parameter.
 * That would work for the switcher and nothing else: it misses a cookie set
 * by anything that is not a page navigation, and it misses the start-up
 * entirely, because the page that renders in the operator's language is
 * rendered by the server from the cookie before any navigation happens.
 *
 * The session's `cookies.on("changed")` event fires for the cookie however it
 * was set — by the switcher, by a `curl`, by the dashboard rewriting it — and
 * it fires for a removal as well as a write.
 *
 * ## WHY THE EVENT'S OWN VALUE IS NOT TRUSTED
 *
 * Chromium emits **two** events when a cookie is overwritten: one carrying
 * the old cookie with `removed: true`, and one carrying the new one. Taking
 * the first event's value at its word would put the menu in the language the
 * operator just left, for as long as it took the second event to arrive.
 * Measured, on an overwrite of `zh-CN` with `en`:
 *
 *     {value: "zh-CN", cause: "overwrite", removed: true}
 *     {value: "en",    cause: "inserted", removed: false}
 *
 * So the handler re-reads the store instead. The store is the state; the
 * event is only a hint that the state may have moved.
 *
 * ## THE SHAPE OF THAT MEASUREMENT, AND WHERE THE FIELDS LIVE
 *
 * The two lines above are a summary, not a literal dump: `cause` and
 * `removed` are **not** properties of the cookie. Electron's
 * `cookies.on("changed")` signature is
 * `(event, cookie, cause, removed)` — three trailing arguments after the
 * cookie, with `cause` one of
 * `"inserted" | "inserted-no-change-overwrite" | "inserted-no-value-change-overwrite" | "explicit" | "overwrite" | "expired" | "evicted" | "expired-overwrite"`
 * and `removed` a boolean. `electron.d.ts` says so, and TypeScript enforces
 * it: reading `cookie.cause` is a compile error, because the field is not
 * there.
 *
 * The handler below takes `cause` and `removed` as named parameters for that
 * reason. It does not use them — the store is the state either way — but
 * naming them is what makes the signature honest, and it is what a reader
 * needs to see in order to know the event carries more than the cookie's own
 * fields.
 *
 * ## WHY THE REMOVAL CASE IS THE ONE THAT MATTERS
 *
 * Chromium reports a removal as a single event carrying the cookie that was
 * **deleted** — the old value, with `removed: true`. So an implementation
 * that believed the event would leave the menu in a language no cookie
 * asks for any more. This is the case `e2e/menu-electron.spec.ts` drives
 * last, and the one its mutation check breaks.
 */

import { Menu, type Session } from "electron";

import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  menuTemplate,
  resolveLocale,
  type Locale,
} from "./menu-policy";

/** The locale the menu is in right now. */
let currentLocale: Locale = DEFAULT_LOCALE;

/** Whether the development-only items are in the menu. */
let debugItems = false;

/**
 * Rebuild the application menu, in `locale`.
 *
 * Electron keeps one application menu, so this replaces it wholesale. The
 * replacement is complete rather than partial on purpose: a menu whose Edit
 * section survived from the previous build and whose View section did not
 * would be a menu in two languages at once.
 */
export function applyMenu(locale: Locale): void {
  currentLocale = locale;
  Menu.setApplicationMenu(Menu.buildFromTemplate(menuTemplate(locale, { debug: debugItems })));
}

/**
 * Install the menu, and say whether the development items belong in it.
 *
 * Called once, at start-up, before the window opens: the menu is in the
 * default language from the first frame, and is corrected a moment later from
 * the cookie, which is not readable until the dashboard's origin is known.
 */
export function installMenu(options: { debug: boolean }): void {
  debugItems = options.debug;
  applyMenu(currentLocale);
}

/**
 * The locale the cookie on `url` says, or the default when it says nothing.
 *
 * A cookie this build does not ship, a cookie that has been cleared, and a
 * cookie that could not be read at all are all the default. The last of those
 * is caught rather than thrown: a menu that cannot read a cookie is a menu in
 * English, and a main process that exits over one is an application that
 * will not start.
 */
export async function readLocaleCookie(target: Session, url: string): Promise<Locale> {
  try {
    const stored = await target.cookies.get({ url, name: LOCALE_COOKIE });
    return resolveLocale(stored[0]?.value);
  } catch (error) {
    console.warn(`[opspilot] could not read ${LOCALE_COOKIE} for ${url}; the menu stays in English`, error);
    return DEFAULT_LOCALE;
  }
}

/**
 * Put the menu in the language the cookie says.
 *
 * A no-op when the cookie names the language the menu is already in, which is
 * the common case: the cookie is written on every visit to the locale route,
 * including a visit that chose the language that was already chosen.
 */
export async function syncMenuToCookie(target: Session, url: string): Promise<Locale> {
  const locale = await readLocaleCookie(target, url);
  if (locale !== currentLocale) {
    applyMenu(locale);
  }
  return locale;
}

/**
 * Follow the cookie for as long as the app is open.
 *
 * The listener is on the session rather than on the window, because the
 * cookie is the session's: the operator's choice outlives the page that
 * recorded it, and a choice made in one page is the one the next page reads.
 */
export function watchLocaleCookie(target: Session, url: string): void {
  // `cause` and `removed` are Electron's third and fourth arguments, not
  // fields on the cookie. They are named so the signature is honest about
  // what the event carries; the handler does not use them, because the store
  // is the state and the event is only a hint that the state may have moved.
  target.cookies.on("changed", (_event, cookie, _cause, _removed) => {
    if (cookie.name !== LOCALE_COOKIE) {
      return;
    }
    // The event says the state may have moved; the store says where it moved
    // to. See the note at the top of this file about the two events Chromium
    // emits for an overwrite — and about the one it emits for a removal,
    // which carries the value that was deleted.
    void syncMenuToCookie(target, url);
  });
}
