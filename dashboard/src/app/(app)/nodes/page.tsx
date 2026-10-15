import Link from "next/link";

import { formatBytes, timeAgo } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { reasonText } from "@/lib/localize";
import { api, can, type Gateway, type Me, type Page, type Peer } from "@/lib/orchestrator";

import { PeerActions } from "./peer-actions";

export const dynamic = "force-dynamic";

export default async function NodesPage({
  searchParams,
}: {
  searchParams: Promise<{ gateway?: string }>;
}) {
  const { gateway = "" } = await searchParams;
  const query = gateway ? `&gateway=${encodeURIComponent(gateway)}` : "";
  const [t, me, peers, gateways] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<Page<Peer>>(`/api/peers/?limit=1000${query}`),
    api<Page<Gateway>>("/api/gateways/"),
  ]);
  const filters = [{ key: "", label: t("nodes.allGateways") }].concat(
    gateways.results.map((g) => ({ key: g.name, label: g.name })),
  );
  return (
    <div className="space-y-4">
      <LiveRefresh types={["anomaly", "peer.status"]} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">{t("nodes.title")}</h1>
        {can(me, "admin") && (
          <Link href="/nodes/new" className="btn btn-primary">
            {t("nodes.new")}
          </Link>
        )}
      </div>
      {gateways.results.length > 1 && (
        <nav className="flex flex-wrap gap-2 text-sm">
          {filters.map((f) => (
            <Link
              key={f.key || "all"}
              href={f.key ? `/nodes?gateway=${f.key}` : "/nodes"}
              className={`btn ${gateway === f.key ? "btn-primary" : "btn-secondary"}`}
            >
              {f.label}
            </Link>
          ))}
        </nav>
      )}
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">{t("nodes.name")}</th>
              <th className="px-4 py-2 font-medium">{t("nodes.gateway")}</th>
              <th className="px-4 py-2 font-medium">{t("nodes.tunnelIp")}</th>
              <th className="px-4 py-2 font-medium">{t("nodes.status")}</th>
              <th className="px-4 py-2 text-right font-medium">{t("nodes.trust")}</th>
              <th className="px-4 py-2 font-medium">{t("nodes.handshake")}</th>
              <th className="px-4 py-2 text-right font-medium">{t("nodes.traffic")}</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {peers.results.map((p) => (
              <tr key={p.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                <td className="px-4 py-2">
                  <Link href={`/nodes/${p.id}`} className="font-medium hover:underline">
                    {p.name}
                  </Link>
                  {p.status_reason && (
                    <div className="text-xs text-zinc-500">{reasonText(t, p.status_reason)}</div>
                  )}
                </td>
                <td className="px-4 py-2">{p.gateway}</td>
                <td className="px-4 py-2 font-mono">{p.wg_ip}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={p.status} label={t.v("peerStatus", p.status)} />
                </td>
                <td className="px-4 py-2 text-right">
                  <TrustScore value={p.trust_score} />
                </td>
                <td className="px-4 py-2">{timeAgo(p.last_handshake_at, t.locale)}</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {formatBytes(p.rx_bytes, t.locale)} / {formatBytes(p.tx_bytes, t.locale)}
                </td>
                <td className="px-4 py-2">
                  <PeerActions
                    id={p.id}
                    status={p.status}
                    canAnalyst={can(me, "analyst")}
                    canAdmin={can(me, "admin")}
                  />
                </td>
              </tr>
            ))}
            {peers.results.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-zinc-500">
                  {t("nodes.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
