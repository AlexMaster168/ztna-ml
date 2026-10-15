import Link from "next/link";

import { ActionForm } from "@/components/action-form";
import { timeAgo } from "@/components/format";
import { StatusBadge } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { deleteGateway } from "@/lib/actions";
import { gatewayState } from "@/lib/gateway-state";
import { api, can, type Gateway, type Me, type Page } from "@/lib/orchestrator";

import { RegisterGatewayForm, RotateTokenForm } from "./gateway-forms";

export const dynamic = "force-dynamic";

export default async function GatewaysPage() {
  const [t, me, gateways] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<Page<Gateway>>("/api/gateways/"),
  ]);
  const admin = can(me, "admin");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("gateways.title")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-500">{t("gateways.intro")}</p>
      </div>
      <section className="card">
        <div className="grid gap-4 p-4 md:grid-cols-2">
          {gateways.results.map((g) => (
            <article key={g.id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <header className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold">{g.name}</h2>
                <StatusBadge status={gatewayState(g)} label={t.v("health", gatewayState(g))} />
                <Link href={`/nodes?gateway=${g.name}`} className="ml-auto text-sm hover:underline">
                  {t("gateways.peers", { n: g.peer_count ?? 0 })}
                </Link>
              </header>
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-zinc-500">{t("gateways.endpoint")}</dt>
                <dd className="font-mono">{g.endpoint}</dd>
                <dt className="text-zinc-500">{t("gateways.subnet")}</dt>
                <dd className="font-mono">{g.wg_subnet}</dd>
                <dt className="text-zinc-500">{t("gateways.publicKey")}</dt>
                <dd className="truncate font-mono text-xs" title={g.public_key}>
                  {g.public_key || t("gateways.notReported")}
                </dd>
                <dt className="text-zinc-500">{t("gateways.revision")}</dt>
                <dd className="tabular-nums">
                  {t("overview.revision", { applied: g.applied_revision, desired: g.revision })}
                </dd>
                <dt className="text-zinc-500">{t("gateways.lastSeen")}</dt>
                <dd>{timeAgo(g.last_seen_at, t.locale)}</dd>
              </dl>
              {admin && (
                <div className="mt-3 flex flex-wrap items-start gap-2">
                  <RotateTokenForm id={g.id} name={g.name} />
                  {!g.peer_count && (
                    <ActionForm action={deleteGateway}>
                      <input type="hidden" name="id" value={g.id} />
                      <button className="btn btn-danger py-1">{t("common.delete")}</button>
                    </ActionForm>
                  )}
                </div>
              )}
            </article>
          ))}
        </div>
        {admin && <RegisterGatewayForm />}
      </section>
    </div>
  );
}
