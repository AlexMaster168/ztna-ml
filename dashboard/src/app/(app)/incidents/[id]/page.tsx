import Link from "next/link";
import { notFound } from "next/navigation";

import { ActionForm } from "@/components/action-form";
import { formatTime } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { incidentAction } from "@/lib/actions";
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
  const me = await api<Me>("/api/me");
  const canAct = can(me, "analyst") && inc.status !== "resolved";

  return (
    <div className="space-y-6">
      <LiveRefresh types={["anomaly"]} peerId={inc.peer} />
      <div>
        <Link href="/incidents" className="text-sm text-zinc-500 hover:underline">
          ← Incidents
        </Link>
        <h1 className="mt-1 flex flex-wrap items-center gap-3 text-2xl font-semibold">
          #{inc.id} {inc.title} <StatusBadge status={inc.severity} /> <StatusBadge status={inc.status} />
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Peer{" "}
          <Link href={`/nodes/${inc.peer}`} className="underline">
            {inc.peer_name}
          </Link>{" "}
          · opened {formatTime(inc.opened_at)} · lowest trust <TrustScore value={inc.min_trust_score} />
          {inc.resolved_at &&
            ` · resolved ${formatTime(inc.resolved_at)} by ${inc.resolved_by ?? "—"}`}
        </p>
        {inc.resolution_note && <p className="mt-2 text-sm">Note: {inc.resolution_note}</p>}
      </div>

      {canAct && (
        <div className="card flex flex-wrap items-center gap-2 p-4">
          <ActionForm action={incidentAction} className="flex flex-1 flex-wrap items-center gap-2">
            <input type="hidden" name="id" value={inc.id} />
            <input type="hidden" name="op" value="resolve" />
            <input name="note" placeholder="Resolution note" className="input max-w-md flex-1" />
            <button className="btn btn-primary">Resolve</button>
          </ActionForm>
          {inc.status === "open" && (
            <ActionForm action={incidentAction}>
              <input type="hidden" name="id" value={inc.id} />
              <input type="hidden" name="op" value="acknowledge" />
              <button className="btn btn-secondary">Acknowledge</button>
            </ActionForm>
          )}
        </div>
      )}

      <section className="card p-4">
        <h2 className="text-sm font-medium">Automatic actions</h2>
        <ol className="mt-2 space-y-1 text-sm">
          {inc.actions.map((a, i) => (
            <li key={i}>
              <span className="tabular-nums text-zinc-500">{formatTime(a.at)}</span>{" "}
              <strong>{a.action}</strong> — {a.reason}
            </li>
          ))}
          {inc.actions.length === 0 && <li className="text-zinc-500">None (monitor only or below threshold)</li>}
        </ol>
      </section>

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 text-sm font-medium">Detections ({inc.events?.length ?? 0})</h2>
        <table className="mt-2 w-full min-w-[560px] text-sm">
          <tbody>
            {inc.events?.map((e) => (
              <tr key={e.id} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="whitespace-nowrap px-4 py-1.5 tabular-nums text-zinc-500">
                  {formatTime(e.created_at)}
                </td>
                <td className="px-2 py-1.5">{e.source}</td>
                <td className="px-2 py-1.5 font-medium">{e.kind.replaceAll("_", " ")}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">−{e.penalty}</td>
                <td className="px-4 py-1.5 font-mono text-xs text-zinc-500">{JSON.stringify(e.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
