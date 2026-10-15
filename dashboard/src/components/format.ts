import type { Locale } from "@/i18n";

const UNITS: Record<Locale, string[]> = {
  uk: ["Б", "КіБ", "МіБ", "ГіБ", "ТіБ"],
  en: ["B", "KiB", "MiB", "GiB", "TiB"],
};

export function formatBytes(n: number, locale: Locale = "uk"): string {
  const u = UNITS[locale];
  if (n < 1024) return `${n} ${u[0]}`;
  let v = n / 1024;
  let i = 1;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${u[i]}`;
}

const AGO: Record<Locale, { never: string; s: string; m: string; h: string; d: string }> = {
  uk: { never: "ніколи", s: "{n} с тому", m: "{n} хв тому", h: "{n} год тому", d: "{n} дн. тому" },
  en: { never: "never", s: "{n}s ago", m: "{n}m ago", h: "{n}h ago", d: "{n}d ago" },
};

export function timeAgo(iso: string | null, locale: Locale = "uk"): string {
  const a = AGO[locale];
  if (!iso) return a.never;
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  const fmt = (tpl: string, n: number) => tpl.replace("{n}", String(n));
  if (s < 60) return fmt(a.s, Math.max(s, 0));
  if (s < 3600) return fmt(a.m, Math.floor(s / 60));
  if (s < 86400) return fmt(a.h, Math.floor(s / 3600));
  return fmt(a.d, Math.floor(s / 86400));
}

// Everything is shown in UTC: server and client components render the same way and
// times match the gateway and orchestrator logs.
const UTC = { timeZone: "UTC", hour12: false } as const;
const INTL: Record<Locale, string> = { uk: "uk-UA", en: "en-GB" };

export function formatTime(iso: string, locale: Locale = "uk"): string {
  return `${new Date(iso).toLocaleString(INTL[locale], UTC)} UTC`;
}

export function clock(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString("en-GB", UTC);
}

export function hhmm(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString("en-GB", {
    ...UTC,
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const pct = (v?: number | null) =>
  v === undefined || v === null ? "—" : `${(v * 100).toFixed(1)}%`;
