import Link from "next/link";

import { formatBytes, timeAgo } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { api, can, type Me, type Page, type Peer } from "@/lib/orchestrator";

import { PeerActions } from "./peer-actions";

export const dynamic = "force-dynamic";

export default async function NodesPage() {
  const [me, peers] = await Promise.all([
    api<Me>("/api/me"),
    api<Page<Peer>>("/api/peers/?limit=1000"),
  ]);
  return (
    <div className="space-y-4">
      <LiveRefresh types={["anomaly", "peer.status"]} />
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Nodes</h1>
        {can(me, "admin") && (
          <Link href="/nodes/new" className="btn btn-primary">
            New peer
          </Link>
        )}
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Tunnel IP</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 text-right font-medium">Trust</th>
              <th className="px-4 py-2 font-medium">Handshake</th>
              <th className="px-4 py-2 text-right font-medium">RX / TX</th>
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
                    <div className="text-xs text-zinc-500">{p.status_reason}</div>
                  )}
                </td>
                <td className="px-4 py-2 font-mono">{p.wg_ip}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={p.status} />
                </td>
                <td className="px-4 py-2 text-right">
                  <TrustScore value={p.trust_score} />
                </td>
                <td className="px-4 py-2">{timeAgo(p.last_handshake_at)}</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {formatBytes(p.rx_bytes)} / {formatBytes(p.tx_bytes)}
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
                <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                  No peers yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
