import type { T } from "@/i18n";

/** The trust engine writes canonical English reasons (the API is language-neutral);
 * known shapes are rendered in the user's language, free text from people as is. */
export function reasonText(t: T, reason: string): string {
  let m = reason.match(/^trust score (\d+) < (\d+)$/);
  if (m) return t("reason.below", { score: m[1], limit: m[2] });
  m = reason.match(/^trust score recovered to (\d+)$/);
  if (m) return t("reason.recovered", { score: m[1] });
  return reason;
}

/** Incident titles are "<detection kind> from <peer>". */
export function incidentTitle(t: T, title: string): string {
  const m = title.match(/^(\S+) from (.+)$/);
  return m ? t("incident.title", { kind: t.v("kind", m[1]), peer: m[2] }) : title;
}

export function describeDetails(t: T, d: Record<string, unknown>): string {
  if (Array.isArray(d.top_features)) {
    const top = (d.top_features as string[]).map((f) => t.v("feature", f)).join(", ");
    return t("details.model", { ratio: String(d.ratio), top });
  }
  return Object.entries(d)
    .map(([k, v]) => `${t.v("detail", k)}: ${Array.isArray(v) ? v.join(", ") : v}`)
    .join(" · ");
}
