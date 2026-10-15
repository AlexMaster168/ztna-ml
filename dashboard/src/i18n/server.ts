import "server-only";

import { cookies, headers } from "next/headers";

import { DEFAULT_LOCALE, LOCALE_COOKIE, isLocale, makeT, type Locale, type T } from ".";

export async function getLocale(): Promise<Locale> {
  const fromCookie = (await cookies()).get(LOCALE_COOKIE)?.value;
  if (isLocale(fromCookie)) return fromCookie;
  // First visit: honour the browser if it prefers English, otherwise Ukrainian.
  const accept = (await headers()).get("accept-language") ?? "";
  return /^en\b/i.test(accept) ? "en" : DEFAULT_LOCALE;
}

export async function getT(): Promise<T> {
  return makeT(await getLocale());
}
