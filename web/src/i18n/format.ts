/**
 * The message lookup: dotted keys, `{placeholder}` interpolation, and plurals.
 *
 * Deliberately small. It is the whole runtime an added locale needs, it is
 * isomorphic (a Server Component and a Client Component build the same `t` from
 * the same function), and it has no dependency -- which is the reason this app
 * has one rather than `next-intl` or `intl-messageformat`. See `server.ts` for
 * that argument in full; the short form is that both of those make locale
 * negotiation and routing part of the framework, and this dashboard's routes,
 * its bookmarkable URLs and its desktop shell's hand-off are all things DoD D8
 * and "do not change behaviour" protect.
 *
 * ## WHY `t` TAKES THE DICTIONARY RATHER THAN FINDING IT
 *
 * A `t` that reached for a global would be unusable in the four `"use client"`
 * components (`decision-actions`, `decision-panel`, `reindex-button`,
 * `ticket-form`): they cannot read a cookie, and importing the English
 * catalogue to do it would ship every string in the dashboard to the browser.
 * So the server resolves the locale and hands each Client Component the slice
 * of copy it actually renders. `createT` is what turns that slice into a `t`.
 */

/**
 * A catalogue: nested objects whose leaves are strings, or plural groups.
 *
 * **A plural is a leaf that happens to be an object**, written as
 * `{"one": ..., "other": ...}` -- the shape every i18n library's plural table
 * has, and the shape a translator already knows. It is distinguishable from a
 * *group* of messages by having an `other` key: no section of this catalogue
 * has a child message called `other`, and `e2e/i18n-catalogue.spec.ts` fails if a
 * future edit gives one, so the discriminator cannot rot into a silent "every
 * group is a plural".
 *
 * The sibling-key encoding (`"replay_one"` beside `"replay_other"`) was tried
 * first and rejected for a concrete reason rather than a taste one: TypeScript
 * will not evaluate `` `${K}_one` extends keyof T `` per-property inside a
 * mapped type -- `K` stays the whole union there, so the check answers for the
 * group rather than the member -- and every plural fell silently out of the
 * derived key union, turning `t("dashboard.replay")` into a `ts(2345)` at every
 * call site. An object plural needs no template literal to be recognised.
 */
export type Messages = { readonly [key: string]: string | Messages };

/** The English catalogue's shape, read as a type and never as a value. */
type EnglishCatalogue = typeof import("./dictionaries/en.json");

/**
 * The dotted path to every leaf in `T`: a plain string, or a plural group.
 *
 * **One walk, not two.** Splitting this into "leaves" and "plurals" and unioning
 * the results makes TypeScript instantiate two recursive walks over the same
 * tree, and at this catalogue's size the second is dropped -- silently, with no
 * error at the definition and a `ts(2345)` at every call site that used a
 * plural. One walk, one union of members, one thing to reason about.
 */
type MessagePaths<T> = T extends string
  ? never
  : {
      [K in keyof T & string]:
        | (T[K] extends { other: string } ? K : never)
        | (T[K] extends string ? K : never)
        | (T[K] extends { other: string } ? never : `${K}.${MessagePaths<T[K]>}`);
    }[keyof T & string];

/**
 * A key of the English catalogue, spelled as it will be passed to `t`.
 *
 * Derived from the source catalogue rather than maintained by hand, so a renamed
 * key is a type error at every call site that used the old name. A plural is
 * passed by its base name (`"dashboard.replay"`); the `_one` / `_other` choice
 * is made by `Intl.PluralRules` inside `resolve`, never by a caller, because
 * which form reads correctly is a property of the language and a caller able to
 * reach past it could pick the wrong one.
 */
export type MessageKey = MessagePaths<EnglishCatalogue>;

/** Values a placeholder can take. `string | number` because counts are numbers. */
export type MessageParams = Readonly<Record<string, string | number>>;

/**
 * What every component gets instead of the strings themselves.
 *
 * The call reads `t("nav.runs")`; the catalogue decides what that is.
 */
export type TFunction = (key: MessageKey, params?: MessageParams) => string;

/**
 * Resolve a dotted key against a catalogue.
 *
 * Returns the string, or -- for a plural key with a `count` -- the form this
 * locale's `Intl.PluralRules` selects for it.
 *
 * Throws on a miss rather than returning the key. A missing message is a bug in
 * the catalogue, and rendering `nav.runs` to an operator who is deciding whether
 * to approve a refund would hide it behind something that looks like copy.
 * `e2e/i18n-catalogue.spec.ts` is the check that should catch it before this
 * does; this is the last line of defence, and it names the key it wanted.
 */
function resolve(
  messages: Messages,
  locale: string,
  key: string,
  params: MessageParams | undefined,
): string {
  const entry = lookup(messages, key);

  if (typeof entry === "string") {
    return entry;
  }
  if (entry === null) {
    throw new Error(`i18n: no message for "${key}" in this catalogue.`);
  }

  const count = params?.count;
  if (count === undefined) {
    throw new Error(`i18n: "${key}" is a plural and was called without a "count" parameter.`);
  }
  const category = pluralCategory(locale, Number(count));
  // The category the language actually uses, then `other` as the floor. Every
  // plural in every catalogue carries `other`: a language whose `Intl`
  // category is not in its own file has nothing else to say, and `other` is the
  // one form guaranteed to exist.
  const forms = entry as Record<string, string>;
  const template = forms[category] ?? forms.other;
  if (template === undefined) {
    throw new Error(
      `i18n: "${key}" has no "${category}" form and no "other" form to fall back to.`,
    );
  }
  return template;
}

/**
 * Walk a dotted path, or answer `null`.
 *
 * `null` rather than a throw because a miss here is a normal outcome the caller
 * resolves: every key `resolve` is asked for exists in the catalogue unless the
 * catalogue is wrong, and "the catalogue is wrong" is reported once, by
 * `resolve`, naming the key.
 */
function lookup(messages: Messages, key: string): string | Messages | null {
  let node: string | Messages = messages;
  for (const segment of key.split(".")) {
    if (typeof node !== "object" || node === null) {
      return null;
    }
    node = (node as Record<string, string | Messages>)[segment];
    if (node === undefined) {
      return null;
    }
  }
  return node;
}

/**
 * The plural category a count falls in, for this locale.
 *
 * `Intl.PluralRules` rather than a hand-written `count === 1` test, because
 * that test is English's rule and not everyone else's -- and a catalogue that
 * is addable later means the *next* language's rule has to be reachable from
 * here without an edit. Node and Chromium both ship it.
 */
function pluralCategory(locale: string, count: number): string {
  try {
    return new Intl.PluralRules(locale).select(count);
  } catch {
    // A locale tag `Intl` does not know still has to render something, and
    // every plural in every catalogue carries `other` as its floor (see
    // `resolve`). Falling to `other` therefore always produces a message, where
    // falling to English's rule could pick a `one` form a language may not have.
    return "other";
  }
}

/** Replace `{name}` with the matching parameter, and leave anything else alone. */
function interpolate(template: string, params: MessageParams | undefined): string {
  if (!params) {
    return template;
  }
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name];
    return value === undefined ? whole : String(value);
  });
}

/**
 * Build a `t` for a catalogue and the locale it is written in.
 *
 * `locale` is the *language* the catalogue is in, not the request's locale: it
 * is what decides the plural category, and a catalogue's plural forms have to be
 * chosen by the language that will read them. Passing `zh-CN` with the English
 * catalogue would pluralise English text by Chinese rules, which is the one
 * combination this function is asked never to produce.
 *
 * A parameter the catalogue does not name is left in place as `{name}` rather
 * than blanked. A visible `{worker}` in a sentence about a claim lease is a
 * defect an operator can report; an empty gap where a worker's name should be is
 * not.
 */
export function createT(messages: Messages, locale: string): TFunction {
  return function t(key, params) {
    return interpolate(resolve(messages, locale, key, params), params);
  };
}

/**
 * A `t` over a *slice* of the English catalogue, for a Client Component.
 *
 * The four `"use client"` components in this app cannot read the locale cookie
 * and must not import a catalogue: `import en from ".../en.json"` inside one
 * would put every string in the dashboard into the browser bundle for the sake
 * of the ten that component renders. So a Server Component passes the sections
 * it needs down as `copy`, this builds the `t`, and the bundle carries only what
 * is rendered there.
 *
 * The `K` keeps the two ends honest -- a component names the sections it needs
 * and TypeScript checks the names against the catalogue -- while the cast to
 * `Messages` is what lets a `TFunction` still be handed back. This is the
 * isomorphic half of the locale layer; `server.ts` is the half that needs a
 * request.
 */
export function createScopedT<K extends keyof EnglishCatalogue>(
  copy: Pick<EnglishCatalogue, K>,
  locale: string,
): TFunction {
  return createT(copy as unknown as Messages, locale);
}