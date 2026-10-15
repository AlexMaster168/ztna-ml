import Link from "next/link";
import { notFound } from "next/navigation";

import { ActionForm } from "@/components/action-form";
import { formatTime } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { incidentAction } from "@/lib/actions";
import { describeDetails, incidentTitle, reasonText } from "@/lib/localize";
import { ApiError, api, can, type Incident, type Me } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let inc: Incident;
  try {
    inc = await api<Incident>(`/api/incidents/${Number(id)}/`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const [t, me] = await Promise.all([getT(), api<Me>("/api/me")]);
  const canAct = can(me, "analyst") && inc.status !== "resolved";

  return (
    <div className="space-y-6">
      <LiveRefresh types={["anomaly"]} peerId={inc.peer} />
      <div>
        <Link href="/incidents" className="text-sm text-zinc-500 hover:underline">
          ← {t("incidents.title")}
        </Link>
        <h1 className="mt-1 flex flex-wrap items-center gap-3 text-2xl font-semibold">
          #{inc.id} {incidentTitle(t, inc.title)}{" "}
          <StatusBadge status={inc.severity} label={t.v("severity", inc.severity)} />{" "}
          <StatusBadge status={inc.status} label={t.v("incStatus", inc.status)} />
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          {t("incidents.peer")}{" "}
          <Link href={`/nodes/${inc.peer}`} className="underline">
            {inc.peer_name}
          </Link>{" "}
          · {t("incident.openedAt", { at: formatTime(inc.opened_at, t.locale) })} ·{" "}
          {t("incident.lowestTrust")} <TrustScore value={inc.min_trust_score} />
          {inc.resolved_at &&
            ` · ${t("incident.resolvedBy", {
              at: formatTime(inc.resolved_at, t.locale),
              who: inc.resolved_by ?? "—",
            })}`}
        </p>
        {inc.resolution_note && (
          <p className="mt-2 text-sm">{t("incident.note", { note: inc.resolution_note })}</p>
        )}
      </div>

      {canAct && (
        <div className="card flex flex-wrap items-center gap-2 p-4">
          <ActionForm action={incidentAction} className="flex flex-1 flex-wrap items-center gap-2">
            <input type="hidden" name="id" value={inc.id} />
            <input type="hidden" name="op" value="resolve" />
            <input
              name="note"
              placeholder={t("incident.notePlaceholder")}
              className="input max-w-md flex-1"
            />
            <button className="btn btn-primary">{t("incident.resolve")}</button>
          </ActionForm>
          {inc.status === "open" && (
            <ActionForm action={incidentAction}>
              <input type="hidden" name="id" value={inc.id} />
              <input type="hidden" name="op" value="acknowledge" />
              <button className="btn btn-secondary">{t("incident.acknowledge")}</button>
            </ActionForm>
          )}
        </div>
      )}

      <section className="card p-4">
        <h2 className="text-sm font-medium">{t("incident.autoActions")}</h2>
        <ol className="mt-2 space-y-1 text-sm">
          {inc.actions.map((a, i) => (
            <li key={i}>
              <span className="tabular-nums text-zinc-500">{formatTime(a.at, t.locale)}</span>{" "}
              <strong>{t.v("autoAction", a.action)}</strong> — {reasonText(t, a.reason)}
            </li>
          ))}
          {inc.actions.length === 0 && <li className="text-zinc-500">{t("incident.noActions")}</li>}
        </ol>
      </section>

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 text-sm font-medium">
          {t("incident.detections", { n: inc.events?.length ?? 0 })}
        </h2>
        <table className="mt-2 w-full min-w-[560px] text-sm">
          <tbody>
            {inc.events?.map((e) => (
              <tr key={e.id} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="whitespace-nowrap px-4 py-1.5 tabular-nums text-zinc-500">
                  {formatTime(e.created_at, t.locale)}
                </td>
                <td className="px-2 py-1.5">{t.v("source", e.source)}</td>
                <td className="px-2 py-1.5 font-medium">{t.v("kind", e.kind)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">−{e.penalty}</td>
                <td className="px-4 py-1.5 text-xs text-zinc-500">{describeDetails(t, e.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
