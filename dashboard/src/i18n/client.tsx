"use client";

import { createContext, useContext, useMemo } from "react";

import { DEFAULT_LOCALE, makeT, type Locale, type T } from ".";

const LocaleContext = createContext<Locale>(DEFAULT_LOCALE);

export function I18nProvider({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

export function useT(): T {
  const locale = useContext(LocaleContext);
  return useMemo(() => makeT(locale), [locale]);
}
