"use server";

import { revalidatePath } from "next/cache";

import { getT } from "@/i18n/server";
import { api, errorMessage, type Peer } from "@/lib/orchestrator";

export type ActionState = { error?: string; config?: string; name?: string };

const TRANSITIONS = ["quarantine", "release", "revoke"] as const;
type Transition = (typeof TRANSITIONS)[number];

export async function changeStatus(_: ActionState, form: FormData): Promise<ActionState> {
  const t = await getT();
  const id = Number(form.get("id"));
  const action = form.get("op") as Transition;
  if (!Number.isInteger(id) || !TRANSITIONS.includes(action)) return { error: t("error.badRequest") };
  try {
    await api(`/api/peers/${id}/${action}/`, {
      method: "POST",
      body: JSON.stringify({ reason: String(form.get("reason") ?? "") }),
    });
  } catch (e) {
    return { error: errorMessage(e, t("error.requestFailed")) };
  }
  revalidatePath("/nodes");
  return {};
}

export async function createPeer(_: ActionState, form: FormData): Promise<ActionState> {
  const t = await getT();
  const name = String(form.get("name") ?? "").trim();
  const publicKey = String(form.get("public_key") ?? "").trim();
  const gateway = String(form.get("gateway") ?? "");
  const groups = form.getAll("groups").map(String);
  try {
    const res = await api<{ peer: Peer; client_config: string | null }>("/api/peers/", {
      method: "POST",
      body: JSON.stringify({ name, public_key: publicKey, ...(gateway ? { gateway } : {}) }),
    });
    if (groups.length) {
      await api(`/api/peers/${res.peer.id}/groups`, {
        method: "POST",
        body: JSON.stringify({ groups }),
      });
    }
    revalidatePath("/nodes");
    return { name: res.peer.name, config: res.client_config ?? undefined };
  } catch (e) {
    return { error: errorMessage(e, t("error.requestFailed")) };
  }
}

export async function setGroups(_: { error?: string; ok?: boolean }, form: FormData) {
  const t = await getT();
  const id = Number(form.get("id"));
  try {
    await api(`/api/peers/${id}/groups`, {
      method: "POST",
      body: JSON.stringify({ groups: form.getAll("groups").map(String) }),
    });
  } catch (e) {
    return { error: errorMessage(e, t("error.requestFailed")) };
  }
  revalidatePath(`/nodes/${id}`);
  return { ok: true };
}
