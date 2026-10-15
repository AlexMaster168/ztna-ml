import { redirect } from "next/navigation";

import { getT } from "@/i18n/server";
import { api, can, type Gateway, type Me, type Page, type PeerGroup } from "@/lib/orchestrator";

import { NewPeerForm } from "./new-peer-form";

export default async function NewPeerPage() {
  const [t, me, gateways, groups] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<Page<Gateway>>("/api/gateways/"),
    api<Page<PeerGroup>>("/api/groups/?limit=200"),
  ]);
  if (!can(me, "admin")) redirect("/nodes");
  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-2xl font-semibold">{t("newPeer.title")}</h1>
      <NewPeerForm
        gateways={gateways.results.map((g) => g.name)}
        groups={groups.results.map((g) => g.name)}
      />
    </div>
  );
}
