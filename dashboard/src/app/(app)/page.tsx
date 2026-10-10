import Link from "next/link";

import { timeAgo } from "@/components/format";
import { StatusBadge } from "@/components/status-badge";
import { LiveFeed, LiveRefresh } from "@/components/live";
import {
  api,
  getHealth,
  type Gateway,
  type Incident,
  type ModelVersion,
  type Page,
  type Peer,
} from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

// A gateway that is behind but checked in recently is applying changes right now.
function gatewayState(g: Gateway): string {
  if (g.in_sync) return "ok";
  const seen = g.last_seen_at ? (Date.now() - new Date(g.last_seen_at).getTime()) / 1000 : Infinity;
  return seen < 30 ? "syncing" : "degraded";
}

export default async function Overview() {
  const [health, peers, gateways, incidents, models] = await Promise.all([
    getHealth(),
    api<Page<Peer>>("/api/peers/?limit=1000"),
    api<Page<Gateway>>("/api/gateways/"),
    api<Page<Incident>>("/api/incidents/?status=open,acknowledged&limit=1"),
    api<Page<ModelVersion>>("/api/models/?limit=50"),
  ]);
  const deployed = models.results.find((m) => m.stage !== "available");
  const count = (s: Peer["status"]) => peers.results.filter((p) => p.status === s).length;
  const tiles = [
    { label: "Active peers", value: count("active") },
    { label: "Quarantined", value: count("quarantined"), warn: count("quarantined") > 0 },
    { label: "Unresolved incidents", value: incidents.count, warn: incidents.count > 0,
      href: "/incidents" },
  ];

  return (
    <div className="space-y-6">
      <LiveRefresh types={["peer.status", "anomaly"]} />
      <h1 className="text-2xl font-semibold">Overview</h1>

      <section className="grid gap-4 sm:grid-cols-3">
        {tiles.map((t) => (
          <Link key={t.label} href={t.href ?? "/nodes"} className="card p-4 hover:border-zinc-400">
            <div className="text-sm text-zinc-500">{t.label}</div>
            <div
              className={`mt-1 text-3xl font-semibold tabular-nums ${t.warn ? "text-amber-600 dark:text-amber-400" : ""}`}
            >
              {t.value}
            </div>
          </Link>
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="card p-4">
          <h2 className="font-medium">Orchestrator</h2>
          <div className="mt-2">
            <StatusBadge status={health.status} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-1 text-sm">
            <dt className="text-zinc-500">Database</dt>
            <dd>{health.db ?? "—"}</dd>
            <dt className="text-zinc-500">Redis</dt>
            <dd>{health.redis ?? "—"}</dd>
          </dl>
        </div>

        {gateways.results.map((g) => (
          <div key={g.id} className="card p-4">
            <h2 className="font-medium">Gateway {g.name}</h2>
            <div className="mt-2">
              <StatusBadge status={gatewayState(g)} />
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-1 text-sm">
              <dt className="text-zinc-500">Endpoint</dt>
              <dd className="font-mono">{g.endpoint}</dd>
              <dt className="text-zinc-500">Revision</dt>
              <dd className="tabular-nums">
                {g.applied_revision} / {g.revision}
              </dd>
              <dt className="text-zinc-500">Last seen</dt>
              <dd>{timeAgo(g.last_seen_at)}</dd>
            </dl>
          </div>
        ))}

        <div className="card p-4">
          <h2 className="font-medium">Detection model</h2>
          <div className="mt-2">
            <StatusBadge status={deployed?.stage ?? "available"} />
          </div>
          <p className="mt-3 text-sm">
            {deployed ? (
              <Link href="/models" className="font-mono hover:underline">
                {deployed.version}
              </Link>
            ) : (
              <span className="text-zinc-500">rules only</span>
            )}
          </p>
        </div>
      </section>

      <LiveFeed />
    </div>
  );
}
