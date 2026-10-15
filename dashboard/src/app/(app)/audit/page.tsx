import { redirect } from "next/navigation";

import { formatTime } from "@/components/format";
import { getT } from "@/i18n/server";
import { api, can, type AuditEntry, type Me, type Page } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const [t, me] = await Promise.all([getT(), api<Me>("/api/me")]);
  if (!can(me, "analyst")) redirect("/");
  const log = await api<Page<AuditEntry>>("/api/audit?limit=200");
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{t("audit.title")}</h1>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">{t("audit.time")}</th>
              <th className="px-4 py-2 font-medium">{t("audit.actor")}</th>
              <th className="px-4 py-2 font-medium">{t("audit.action")}</th>
              <th className="px-4 py-2 font-medium">{t("audit.target")}</th>
              <th className="px-4 py-2 font-medium">{t("audit.details")}</th>
            </tr>
          </thead>
          <tbody>
            {log.results.map((a) => (
              <tr key={a.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                <td className="whitespace-nowrap px-4 py-2 tabular-nums">
                  {formatTime(a.created_at, t.locale)}
                </td>
                <td className="px-4 py-2">{a.actor_label}</td>
                <td className="px-4 py-2">
                  {t.v("audit", a.action)}
                  <div className="font-mono text-xs text-zinc-500">{a.action}</div>
                </td>
                <td className="px-4 py-2 font-mono text-xs">
                  {a.target_type}#{a.target_id}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-zinc-500">
                  {Object.keys(a.data).length ? JSON.stringify(a.data) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
