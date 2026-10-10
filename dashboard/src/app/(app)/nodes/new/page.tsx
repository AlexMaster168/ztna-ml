import { redirect } from "next/navigation";

import { api, can, type Me } from "@/lib/orchestrator";

import { NewPeerForm } from "./new-peer-form";

export default async function NewPeerPage() {
  const me = await api<Me>("/api/me");
  if (!can(me, "admin")) redirect("/nodes");
  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-2xl font-semibold">New peer</h1>
      <NewPeerForm />
    </div>
  );
}
