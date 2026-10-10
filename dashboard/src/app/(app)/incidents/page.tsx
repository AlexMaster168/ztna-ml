import Link from "next/link";

import { timeAgo } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { api, type Incident, type Page } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const FILTERS = [
  { key: "open,acknowledged", label: "Unresolved" },
  { key: "resolved", label: "Resolved" },
  { key: "", label: "All" },
];

export default async function IncidentsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status = "open,acknowledged" } = await searchParams;
  const query = status ? `&status=${encodeURIComponent(status)}` : "";
  const incidents = await api<Page<Incident>>(`/api/incidents/?limit=200${query}`);
  return (
    <div className="space-y-4">
      <LiveRefresh types={["anomaly", "peer.status"]} />
      <h1 className="text-2xl font-semibold">Incidents</h1>
      <nav className="flex gap-2 text-sm">
        {FILTERS.map((f) => (
          <Link
            key={f.label}
            href={`/incidents?status=${f.key}`}
            className={`btn ${status === f.key ? "btn-primary" : "btn-secondary"}`}
          >
            {f.label}
          </Link>
        ))}
      </nav>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">Incident</th>
              <th className="px-4 py-2 font-medium">Peer</th>
              <th className="px-4 py-2 font-medium">Severity</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 text-right font-medium">Min trust</th>
              <th className="px-4 py-2 font-medium">Auto actions</th>
              <th className="px-4 py-2 font-medium">Opened</th>
            </tr>
          </thead>
          <tbody>
            {incidents.results.map((i) => (
              <tr key={i.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                <td className="px-4 py-2">
                  <Link href={`/incidents/${i.id}`} className="font-medium hover:underline">
                    #{i.id} {i.title}
                  </Link>
                  <div className="text-xs text-zinc-500">{i.event_count} detections</div>
                </td>
                <td className="px-4 py-2">
                  <Link href={`/nodes/${i.peer}`} className="hover:underline">
                    {i.peer_name}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={i.severity} />
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={i.status} />
                </td>
                <td className="px-4 py-2 text-right">
                  <TrustScore value={i.min_trust_score} />
                </td>
                <td className="px-4 py-2 text-xs">{i.actions.map((a) => a.action).join(" → ") || "—"}</td>
                <td className="px-4 py-2">{timeAgo(i.opened_at)}</td>
              </tr>
            ))}
            {incidents.results.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                  Nothing here
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
