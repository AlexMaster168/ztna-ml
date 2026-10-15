import Link from "next/link";

import { timeAgo } from "@/components/format";
import { LiveFeed, LiveRefresh } from "@/components/live";
import { StatusBadge } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { gatewayState } from "@/lib/gateway-state";
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

export default async function Overview() {
  const [t, health, peers, gateways, incidents, models] = await Promise.all([
    getT(),
    getHealth(),
    api<Page<Peer>>("/api/peers/?limit=1000"),
    api<Page<Gateway>>("/api/gateways/"),
    api<Page<Incident>>("/api/incidents/?status=open,acknowledged&limit=1"),
    api<Page<ModelVersion>>("/api/models/?limit=50"),
  ]);
  const active = models.results.find((m) => m.stage === "active");
  const shadow = models.results.find((m) => m.stage === "shadow");
  const count = (s: Peer["status"]) => peers.results.filter((p) => p.status === s).length;
  const restricted = peers.results.filter((p) => p.status === "active" && p.trust_score < 80).length;
  const tiles = [
    { label: t("overview.activePeers"), value: count("active") },
    { label: t("overview.restricted"), value: restricted, warn: restricted > 0, href: "/access" },
    { label: t("overview.quarantined"), value: count("quarantined"), warn: count("quarantined") > 0 },
    {
      label: t("overview.unresolved"),
      value: incidents.count,
      warn: incidents.count > 0,
      href: "/incidents",
    },
  ];

  return (
    <div className="space-y-6">
      <LiveRefresh types={["peer.status", "anomaly", "peer.access"]} />
      <h1 className="text-2xl font-semibold">{t("overview.title")}</h1>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((tile) => (
          <Link key={tile.label} href={tile.href ?? "/nodes"} className="card p-4 hover:border-zinc-400">
            <div className="text-sm text-zinc-500">{tile.label}</div>
            <div
              className={`mt-1 text-3xl font-semibold tabular-nums ${tile.warn ? "text-amber-600 dark:text-amber-400" : ""}`}
            >
              {tile.value}
            </div>
          </Link>
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <div className="card p-4">
          <h2 className="font-medium">{t("overview.orchestrator")}</h2>
          <div className="mt-2">
            <StatusBadge status={health.status} label={t.v("health", health.status)} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-1 text-sm">
            <dt className="text-zinc-500">{t("overview.database")}</dt>
            <dd>{health.db ?? "—"}</dd>
            <dt className="text-zinc-500">Redis</dt>
            <dd>{health.redis ?? "—"}</dd>
          </dl>
        </div>

        <div className="card p-4">
          <h2 className="font-medium">
            <Link href="/gateways" className="hover:underline">
              {t("overview.gateways")}
            </Link>
          </h2>
          <ul className="mt-2 space-y-2 text-sm">
            {gateways.results.map((g) => (
              <li key={g.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{g.name}</span>
                <StatusBadge status={gatewayState(g)} label={t.v("health", gatewayState(g))} />
                <span className="ml-auto tabular-nums text-zinc-500">
                  {t("overview.revision", { applied: g.applied_revision, desired: g.revision })} ·{" "}
                  {timeAgo(g.last_seen_at, t.locale)}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="card p-4">
          <h2 className="font-medium">
            <Link href="/models" className="hover:underline">
              {t("overview.model")}
            </Link>
          </h2>
          <dl className="mt-2 space-y-2 text-sm">
            {[
              { m: active, stage: "active" },
              { m: shadow, stage: "shadow" },
            ].map(({ m, stage }) => (
              <div key={stage} className="flex flex-wrap items-center gap-2">
                <dt>
                  <StatusBadge status={stage} label={t.v("stage", stage)} />
                </dt>
                <dd className="truncate font-mono text-xs">
                  {m ? (
                    <Link href={`/models/${m.id}`} className="hover:underline">
                      {m.version}
                    </Link>
                  ) : (
                    <span className="text-zinc-500">
                      {stage === "active" ? t("overview.rulesOnly") : "—"}
                    </span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <LiveFeed />
    </div>
  );
}
