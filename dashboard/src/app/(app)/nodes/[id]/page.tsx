import Link from "next/link";
import { notFound } from "next/navigation";

import { ActionForm } from "@/components/action-form";
import { TimeSeriesChart } from "@/components/chart";
import { clock, formatBytes, formatTime, timeAgo } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { setAllowlist } from "@/lib/actions";
import { describeDetails, incidentTitle, reasonText } from "@/lib/localize";
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
  type PeerAccess,
  type PeerGroup,
  type PeerMetrics,
} from "@/lib/orchestrator";

import { setGroups } from "../actions";
import { PeerActions } from "../peer-actions";

export const dynamic = "force-dynamic";

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
  const [t, me, scores, metrics, events, incidents, cfg, access, groups] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<{ score: number; created_at: string }[]>(`/api/peers/${peerId}/scores?minutes=60`),
    api<PeerMetrics>(`/api/peers/${peerId}/metrics`),
    api<AnomalyEvent[]>(`/api/events?peer=${peerId}&limit=12`),
    api<Page<Incident>>(`/api/incidents/?peer=${peerId}`),
    api<EnforcementSettings>("/api/settings/enforcement"),
    api<PeerAccess>(`/api/peers/${peerId}/access`),
    api<Page<PeerGroup>>("/api/groups/?limit=200"),
  ]);
  const admin = can(me, "admin");

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
      <LiveRefresh types={["anomaly", "peer.status", "peer.access"]} peerId={peerId} />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/nodes" className="text-sm text-zinc-500 hover:underline">
            ← {t("nodes.title")}
          </Link>
          <h1 className="mt-1 flex items-center gap-3 text-2xl font-semibold">
            {peer.name} <StatusBadge status={peer.status} label={t.v("peerStatus", peer.status)} />
            {peer.allowlisted && <StatusBadge status="allowlisted" label={t("peer.allowlisted")} />}
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            {t("peer.summary", {
              ip: peer.wg_ip,
              gateway: peer.gateway,
              handshake: timeAgo(peer.last_handshake_at, t.locale),
              rx: formatBytes(peer.rx_bytes, t.locale),
              tx: formatBytes(peer.tx_bytes, t.locale),
            })}
          </p>
          {peer.status_reason && (
            <p className="mt-1 text-sm">
              {t("peer.reason", { reason: reasonText(t, peer.status_reason) })}
              {peer.quarantined_by_system && ` ${t("peer.automatic")}`}
            </p>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="text-right">
            <div className="text-xs text-zinc-500">{t("peer.trustScore")}</div>
            <div className="text-4xl font-semibold">
              <TrustScore value={peer.trust_score} />
            </div>
          </div>
          <PeerActions
            id={peer.id}
            status={peer.status}
            canAnalyst={can(me, "analyst")}
            canAdmin={admin}
          />
          {admin && peer.status !== "revoked" && (
            <ActionForm action={setAllowlist}>
              <input type="hidden" name="id" value={peer.id} />
              <input type="hidden" name="allowlisted" value={String(!peer.allowlisted)} />
              <button className="btn btn-secondary">
                {peer.allowlisted ? t("peer.unallowlist") : t("peer.allowlist")}
              </button>
            </ActionForm>
          )}
        </div>
      </div>

      <section className="card overflow-x-auto">
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4">
          <h2 className="text-sm font-medium">{t("peer.access")}</h2>
          <span className="text-xs text-zinc-500">
            {t("peer.groups")}: {access.groups.length ? access.groups.join(", ") : "—"}
          </span>
        </div>
        <table className="mt-2 w-full min-w-[560px] text-sm">
          <thead className="text-left text-zinc-500">
            <tr>
              <th className="px-4 py-1.5 font-medium">{t("access.resource")}</th>
              <th className="px-4 py-1.5 font-medium">{t("access.path")}</th>
              <th className="px-4 py-1.5 text-right font-medium">{t("access.minTrust")}</th>
              <th className="px-4 py-1.5 font-medium">{t("access.decision")}</th>
            </tr>
          </thead>
          <tbody>
            {access.resources.map((r) => (
              <tr key={r.resource} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="px-4 py-1.5 font-medium">{r.resource}</td>
                <td className="px-4 py-1.5 font-mono">{r.path_prefix}</td>
                <td className="px-4 py-1.5 text-right tabular-nums">{r.min_trust}</td>
                <td className="px-4 py-1.5">
                  <StatusBadge status={r.decision} label={t.v("decision", r.decision)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {admin && groups.results.length > 0 && (
          <div className="border-t border-zinc-100 px-4 py-3 dark:border-zinc-800">
            <ActionForm action={setGroups} className="flex flex-wrap items-center gap-3 text-sm">
              <input type="hidden" name="id" value={peer.id} />
              <span className="text-zinc-500">{t("peer.editGroups")}</span>
              {groups.results.map((g) => (
                <label key={g.id} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    name="groups"
                    value={g.name}
                    defaultChecked={access.groups.includes(g.name)}
                  />
                  {g.name}
                </label>
              ))}
              <button className="btn btn-secondary">{t("common.save")}</button>
            </ActionForm>
          </div>
        )}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <TimeSeriesChart
          title={t("peer.chart.trust")}
          data={scoreSeries}
          step
          yMax={100}
          references={[
            { y: cfg.quarantine_below, label: t("peer.chart.quarantine", { v: cfg.quarantine_below }) },
            { y: cfg.release_above, label: t("peer.chart.release", { v: cfg.release_above }) },
          ]}
        />
        <TimeSeriesChart title={t("peer.chart.requests")} data={requests} kind="bar" />
        <TimeSeriesChart
          title={t("peer.chart.ratio")}
          data={ratios}
          yMax={5}
          references={[{ y: 1, label: t("peer.chart.threshold") }]}
          decimals={2}
        />
        <div className="card overflow-hidden">
          <h2 className="px-4 pt-4 text-sm font-medium">{t("nav.incidents")}</h2>
          <ul className="mt-2 divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {incidents.results.slice(0, 8).map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
                <StatusBadge status={i.severity} label={t.v("severity", i.severity)} />
                <Link href={`/incidents/${i.id}`} className="font-medium hover:underline">
                  #{i.id} {incidentTitle(t, i.title)}
                </Link>
                <StatusBadge status={i.status} label={t.v("incStatus", i.status)} />
                <span className="ml-auto text-zinc-500">{timeAgo(i.opened_at, t.locale)}</span>
              </li>
            ))}
            {incidents.results.length === 0 && (
              <li className="px-4 py-6 text-center text-zinc-500">{t("peer.noIncidents")}</li>
            )}
          </ul>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card overflow-x-auto">
          <h2 className="px-4 pt-4 text-sm font-medium">{t("peer.detections")}</h2>
          <table className="mt-2 w-full min-w-[480px] text-sm">
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-t border-zinc-100 dark:border-zinc-800">
                  <td className="whitespace-nowrap px-4 py-1.5 tabular-nums text-zinc-500">
                    {formatTime(e.created_at, t.locale)}
                  </td>
                  <td className="px-2 py-1.5">{t.v("source", e.source)}</td>
                  <td className="px-2 py-1.5 font-medium">{t.v("kind", e.kind)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">−{e.penalty}</td>
                  <td className="max-w-[220px] truncate px-4 py-1.5 text-xs text-zinc-500">
                    {describeDetails(t, e.details)}
                  </td>
                </tr>
              ))}
              {events.length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-center text-zinc-500">{t("peer.noDetections")}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card overflow-x-auto">
          <h2 className="px-4 pt-4 text-sm font-medium">{t("peer.recent")}</h2>
          <table className="mt-2 w-full min-w-[480px] text-sm">
            <tbody>
              {metrics.recent_requests.slice(0, 25).map((r, i) => (
                <tr key={`${r.ts}-${i}`} className="border-t border-zinc-100 dark:border-zinc-800">
                  <td className="whitespace-nowrap px-4 py-1 tabular-nums text-zinc-500">
                    {clock(r.ts)}
                  </td>
                  <td className="px-2 py-1 font-mono">{r.method}</td>
                  <td className="max-w-[180px] truncate px-2 py-1 font-mono">{r.path}</td>
                  <td className="px-2 py-1 tabular-nums">
                    {r.status}
                    {r.denied && (
                      <span className="ml-1 text-xs text-amber-700 dark:text-amber-400">
                        {t("peer.policyDenied")}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {formatBytes(r.bytes_sent, t.locale)}
                  </td>
                  <td className="max-w-[160px] truncate px-4 py-1 text-xs text-zinc-500" title={r.ua}>
                    {r.ua}
                  </td>
                </tr>
              ))}
              {metrics.recent_requests.length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-center text-zinc-500">{t("peer.noTraffic")}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
