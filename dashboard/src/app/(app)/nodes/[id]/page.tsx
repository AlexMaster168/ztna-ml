import Link from "next/link";
import { notFound } from "next/navigation";

import { ActionForm } from "@/components/action-form";
import { TimeSeriesChart } from "@/components/chart";
import { clock, formatBytes, formatTime, timeAgo } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { setAllowlist } from "@/lib/actions";
import {
  ApiError,
  api,
  can,
  type AnomalyEvent,
  type EnforcementSettings,
  type Incident,
  type Me,
  type Page,
  type Peer,
  type PeerMetrics,
} from "@/lib/orchestrator";

import { PeerActions } from "../peer-actions";

export const dynamic = "force-dynamic";

function describeDetails(d: Record<string, unknown>): string {
  if (Array.isArray(d.top_features)) {
    return `ratio ${d.ratio} · ${(d.top_features as string[]).join(", ")}`;
  }
  return Object.entries(d)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${Array.isArray(v) ? v.join(", ") : v}`)
    .join(" · ");
}

export default async function NodePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const peerId = Number(id);
  if (!Number.isInteger(peerId)) notFound();

  let peer: Peer;
  try {
    peer = await api<Peer>(`/api/peers/${peerId}/`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const [me, scores, metrics, events, incidents, cfg] = await Promise.all([
    api<Me>("/api/me"),
    api<{ score: number; created_at: string }[]>(`/api/peers/${peerId}/scores?minutes=60`),
    api<PeerMetrics>(`/api/peers/${peerId}/metrics`),
    api<AnomalyEvent[]>(`/api/events?peer=${peerId}&limit=12`),
    api<Page<Incident>>(`/api/incidents/?peer=${peerId}`),
    api<EnforcementSettings>("/api/settings/enforcement"),
  ]);

  // Score history is change-only; extend the last value to now so the line is current.
  const now = Date.now() / 1000;
  const scoreSeries = scores.map((s) => ({ t: new Date(s.created_at).getTime() / 1000, v: s.score }));
  if (scoreSeries.length) scoreSeries.push({ t: now, v: peer.trust_score });
  const requests = metrics.history.map((w) => ({ t: w.window_start, v: w.requests }));
  const ratios = metrics.history
    .filter((w) => w.anomaly_ratio !== null)
    .map((w) => ({ t: w.window_start, v: w.anomaly_ratio as number }));

  return (
    <div className="space-y-6">
      <LiveRefresh types={["anomaly", "peer.status"]} peerId={peerId} />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/nodes" className="text-sm text-zinc-500 hover:underline">
            ← Nodes
          </Link>
          <h1 className="mt-1 flex items-center gap-3 text-2xl font-semibold">
            {peer.name} <StatusBadge status={peer.status} />
            {peer.allowlisted && <StatusBadge status="allowlisted" />}
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            <span className="font-mono">{peer.wg_ip}</span> · gateway {peer.gateway} · handshake{" "}
            {timeAgo(peer.last_handshake_at)} · RX {formatBytes(peer.rx_bytes)} / TX{" "}
            {formatBytes(peer.tx_bytes)}
          </p>
          {peer.status_reason && (
            <p className="mt-1 text-sm">
              Reason: {peer.status_reason}
              {peer.quarantined_by_system && " (automatic)"}
            </p>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="text-right">
            <div className="text-xs text-zinc-500">Trust score</div>
            <div className="text-4xl font-semibold">
              <TrustScore value={peer.trust_score} />
            </div>
          </div>
          <PeerActions
            id={peer.id}
            status={peer.status}
            canAnalyst={can(me, "analyst")}
            canAdmin={can(me, "admin")}
          />
          {can(me, "admin") && peer.status !== "revoked" && (
            <ActionForm action={setAllowlist}>
              <input type="hidden" name="id" value={peer.id} />
              <input type="hidden" name="allowlisted" value={String(!peer.allowlisted)} />
              <button className="btn btn-secondary">
                {peer.allowlisted ? "Remove from allowlist" : "Allowlist"}
              </button>
            </ActionForm>
          )}
        </div>
      </div>

      <section className="grid gap-4 lg:grid-cols-2">
        <TimeSeriesChart
          title="Trust score, last hour"
          data={scoreSeries}
          yMax={100}
          references={[
            { y: cfg.quarantine_below, label: `quarantine < ${cfg.quarantine_below}` },
            { y: cfg.release_above, label: `release ≥ ${cfg.release_above}` },
          ]}
        />
        <TimeSeriesChart title="Requests per 30 s window" data={requests} kind="bar" />
        <TimeSeriesChart
          title="Model anomaly ratio (≥ 1 is anomalous, axis capped at 5)"
          data={ratios}
          yMax={5}
          references={[{ y: 1, label: "threshold" }]}
          decimals={2}
        />
        <div className="card overflow-hidden">
          <h2 className="px-4 pt-4 text-sm font-medium">Incidents</h2>
          <ul className="mt-2 divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {incidents.results.slice(0, 8).map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
                <StatusBadge status={i.severity} />
                <Link href={`/incidents/${i.id}`} className="font-medium hover:underline">
                  #{i.id} {i.title}
                </Link>
                <StatusBadge status={i.status} />
                <span className="ml-auto text-zinc-500">{timeAgo(i.opened_at)}</span>
              </li>
            ))}
            {incidents.results.length === 0 && (
              <li className="px-4 py-6 text-center text-zinc-500">No incidents</li>
            )}
          </ul>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card overflow-x-auto">
          <h2 className="px-4 pt-4 text-sm font-medium">Detections</h2>
          <table className="mt-2 w-full min-w-[480px] text-sm">
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-t border-zinc-100 dark:border-zinc-800">
                  <td className="whitespace-nowrap px-4 py-1.5 tabular-nums text-zinc-500">
                    {formatTime(e.created_at)}
                  </td>
                  <td className="px-2 py-1.5">{e.source}</td>
                  <td className="px-2 py-1.5 font-medium">{e.kind.replaceAll("_", " ")}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">−{e.penalty}</td>
                  <td className="max-w-[220px] truncate px-4 py-1.5 text-xs text-zinc-500">
                    {describeDetails(e.details)}
                  </td>
                </tr>
              ))}
              {events.length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-center text-zinc-500">No detections</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card overflow-x-auto">
          <h2 className="px-4 pt-4 text-sm font-medium">Recent requests</h2>
          <table className="mt-2 w-full min-w-[480px] text-sm">
            <tbody>
              {metrics.recent_requests.slice(0, 25).map((r, i) => (
                <tr key={`${r.ts}-${i}`} className="border-t border-zinc-100 dark:border-zinc-800">
                  <td className="whitespace-nowrap px-4 py-1 tabular-nums text-zinc-500">
                    {clock(r.ts)}
                  </td>
                  <td className="px-2 py-1 font-mono">{r.method}</td>
                  <td className="max-w-[180px] truncate px-2 py-1 font-mono">{r.path}</td>
                  <td className="px-2 py-1 tabular-nums">{r.status}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatBytes(r.bytes_sent)}</td>
                  <td className="max-w-[160px] truncate px-4 py-1 text-xs text-zinc-500" title={r.ua}>
                    {r.ua}
                  </td>
                </tr>
              ))}
              {metrics.recent_requests.length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-center text-zinc-500">No recent traffic</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
