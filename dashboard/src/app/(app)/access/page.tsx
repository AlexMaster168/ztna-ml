import Link from "next/link";

import { ActionForm } from "@/components/action-form";
import { LiveRefresh } from "@/components/live";
import { StatusBadge, TrustScore } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import {
  createGroup,
  createResource,
  deleteGroup,
  deleteResource,
  updateResource,
} from "@/lib/actions";
import { api, can, type Me, type Page, type Peer, type PeerGroup, type Resource } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

export default async function AccessPage() {
  const [t, me, resources, groups, peers] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<Page<Resource>>("/api/resources/?limit=200"),
    api<Page<PeerGroup>>("/api/groups/?limit=200"),
    api<Page<Peer>>("/api/peers/?limit=1000"),
  ]);
  const admin = can(me, "admin");
  const peerName = new Map(peers.results.map((p) => [p.id, p.name]));
  const memberOf = (p: Peer) => new Set(groups.results.filter((g) => g.peers.includes(p.id)).map((g) => g.name));
  // Peers that currently lose at least one resource they would otherwise be entitled to.
  const restricted = peers.results
    .filter((p) => p.status === "active")
    .map((p) => {
      const mine = memberOf(p);
      const lost = resources.results.filter(
        (r) =>
          r.enabled &&
          p.trust_score < r.min_trust &&
          (r.groups.length === 0 || r.groups.some((g) => mine.has(g))),
      );
      return { peer: p, lost };
    })
    .filter((x) => x.lost.length > 0);

  return (
    <div className="space-y-6">
      <LiveRefresh types={["peer.access", "peer.status"]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("access.title")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-500">{t("access.intro")}</p>
      </div>

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 font-medium">{t("access.resources")}</h2>
        <table className="mt-2 w-full min-w-[900px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">{t("access.resource")}</th>
              <th className="px-4 py-2 font-medium">{t("access.path")}</th>
              <th className="px-4 py-2 font-medium">{t("access.upstream")}</th>
              <th className="px-4 py-2 font-medium">{t("access.policy")}</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {resources.results.map((r) => (
              <tr key={r.id} className="border-b border-zinc-100 align-top last:border-0 dark:border-zinc-800">
                <td className="px-4 py-2">
                  <div className="font-medium">{r.name}</div>
                  <div className="text-xs text-zinc-500">{r.description}</div>
                </td>
                <td className="px-4 py-2 font-mono">{r.path_prefix}</td>
                <td className="px-4 py-2 font-mono text-xs">{r.upstream}</td>
                <td className="px-4 py-2">
                  {admin ? (
                    <ActionForm action={updateResource} className="flex flex-wrap items-center gap-2">
                      <input type="hidden" name="id" value={r.id} />
                      <label className="flex items-center gap-1 text-xs">
                        {t("access.trustAtLeast")}
                        <input
                          name="min_trust"
                          type="number"
                          min="0"
                          max="100"
                          defaultValue={r.min_trust}
                          className="input w-16 py-1"
                        />
                      </label>
                      {groups.results.map((g) => (
                        <label key={g.id} className="flex items-center gap-1 text-xs">
                          <input
                            type="checkbox"
                            name="groups"
                            value={g.name}
                            defaultChecked={r.groups.includes(g.name)}
                          />
                          {g.name}
                        </label>
                      ))}
                      <label className="flex items-center gap-1 text-xs">
                        <input type="checkbox" name="enabled" defaultChecked={r.enabled} />
                        {t("access.enabled")}
                      </label>
                      <button className="btn btn-secondary py-1">{t("common.save")}</button>
                    </ActionForm>
                  ) : (
                    <span className="text-xs">
                      {t("access.policyText", {
                        trust: r.min_trust,
                        groups: r.groups.length ? r.groups.join(", ") : t("access.everyone"),
                      })}
                      {!r.enabled && ` · ${t("access.disabled")}`}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-right">
                  {admin && r.path_prefix !== "/" && (
                    <ActionForm action={deleteResource}>
                      <input type="hidden" name="id" value={r.id} />
                      <button className="btn btn-danger py-1">{t("common.delete")}</button>
                    </ActionForm>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {admin && (
          <ActionForm
            action={createResource}
            className="grid gap-3 border-t border-zinc-100 p-4 sm:grid-cols-3 dark:border-zinc-800"
          >
            <h3 className="text-sm font-medium sm:col-span-3">{t("access.publish")}</h3>
            <input name="name" required placeholder={t("access.namePh")} className="input" />
            <input name="path_prefix" required placeholder="/wiki" className="input font-mono" />
            <input
              name="upstream"
              required
              placeholder="http://protected-app:8000"
              className="input font-mono"
            />
            <input name="description" placeholder={t("access.descriptionPh")} className="input sm:col-span-2" />
            <label className="flex items-center gap-2 text-sm">
              {t("access.trustAtLeast")}
              <input name="min_trust" type="number" min="0" max="100" defaultValue={50} className="input w-20" />
            </label>
            <div className="flex flex-wrap items-center gap-3 text-sm sm:col-span-3">
              <span className="text-zinc-500">{t("access.onlyGroups")}</span>
              {groups.results.map((g) => (
                <label key={g.id} className="flex items-center gap-1">
                  <input type="checkbox" name="groups" value={g.name} /> {g.name}
                </label>
              ))}
              <input type="hidden" name="enabled" value="on" />
              <button className="btn btn-primary ml-auto">{t("access.publishButton")}</button>
            </div>
          </ActionForm>
        )}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card overflow-x-auto">
          <h2 className="px-4 pt-4 font-medium">{t("access.restricted")}</h2>
          <p className="px-4 text-xs text-zinc-500">{t("access.restrictedHint")}</p>
          <ul className="mt-2 divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {restricted.map(({ peer, lost }) => (
              <li key={peer.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
                <Link href={`/nodes/${peer.id}`} className="font-medium hover:underline">
                  {peer.name}
                </Link>
                <TrustScore value={peer.trust_score} />
                <span className="ml-auto flex flex-wrap gap-1">
                  {lost.map((r) => (
                    <StatusBadge key={r.id} status="low_trust" label={`${r.name} ≥ ${r.min_trust}`} />
                  ))}
                </span>
              </li>
            ))}
            {restricted.length === 0 && (
              <li className="px-4 py-6 text-center text-zinc-500">{t("access.noneRestricted")}</li>
            )}
          </ul>
        </div>

        <div className="card overflow-x-auto">
          <h2 className="px-4 pt-4 font-medium">{t("access.groups")}</h2>
          <ul className="mt-2 divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {groups.results.map((g) => (
              <li key={g.id} className="flex flex-wrap items-start gap-2 px-4 py-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{g.name}</div>
                  <div className="text-xs text-zinc-500">{g.description}</div>
                  <div className="mt-1 text-xs">
                    {t("access.members", { n: g.peers.length })}:{" "}
                    {g.peers.map((p) => peerName.get(p) ?? `#${p}`).join(", ") || "—"}
                  </div>
                  {g.resources.length > 0 && (
                    <div className="text-xs text-zinc-500">
                      {t("access.groupResources", { list: g.resources.join(", ") })}
                    </div>
                  )}
                </div>
                {admin && (
                  <ActionForm action={deleteGroup}>
                    <input type="hidden" name="id" value={g.id} />
                    <button className="btn btn-danger py-1">{t("common.delete")}</button>
                  </ActionForm>
                )}
              </li>
            ))}
            {groups.results.length === 0 && (
              <li className="px-4 py-6 text-center text-zinc-500">{t("common.nothing")}</li>
            )}
          </ul>
          {admin && (
            <ActionForm
              action={createGroup}
              className="flex flex-wrap gap-2 border-t border-zinc-100 p-4 dark:border-zinc-800"
            >
              <input name="name" required pattern="[a-z0-9_-]+" placeholder="contractors" className="input max-w-40" />
              <input name="description" placeholder={t("access.descriptionPh")} className="input flex-1" />
              <button className="btn btn-primary">{t("access.addGroup")}</button>
            </ActionForm>
          )}
        </div>
      </section>
    </div>
  );
}
