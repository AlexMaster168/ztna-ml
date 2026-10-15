import { en, uk, type MessageKey } from "./messages";

export type { MessageKey };
export type Locale = "uk" | "en";
export const LOCALES: Locale[] = ["uk", "en"];
export const DEFAULT_LOCALE: Locale = "uk";
export const LOCALE_COOKIE = "ztna_lang";

const catalogs: Record<Locale, Record<string, string>> = { uk, en };

export const isLocale = (v: unknown): v is Locale => v === "uk" || v === "en";

export type Vars = Record<string, string | number>;

function fill(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m));
}

export type T = {
  (key: MessageKey, vars?: Vars): string;
  /** Translate an enum-like value from the API ("status.quarantined"); unknown values
   * fall back to the raw value with underscores as spaces. */
  v: (group: string, value: string | null | undefined) => string;
  locale: Locale;
};

export function makeT(locale: Locale): T {
  const cat = catalogs[locale];
  const t = ((key: MessageKey, vars?: Vars) => fill(cat[key] ?? en[key] ?? key, vars)) as T;
  t.v = (group, value) => {
    if (value === null || value === undefined || value === "") return "—";
    return cat[`${group}.${value}`] ?? value.replaceAll("_", " ");
  };
  t.locale = locale;
  return t;
}
