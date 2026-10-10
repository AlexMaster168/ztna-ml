"use server";

import { revalidatePath } from "next/cache";

import { ApiError, api, type Peer } from "@/lib/orchestrator";

export type ActionState = { error?: string; config?: string; name?: string };

const TRANSITIONS = ["quarantine", "release", "revoke"] as const;
type Transition = (typeof TRANSITIONS)[number];

export async function changeStatus(_: ActionState, form: FormData): Promise<ActionState> {
  const id = Number(form.get("id"));
  const action = form.get("op") as Transition;
  if (!Number.isInteger(id) || !TRANSITIONS.includes(action)) return { error: "Bad request" };
  try {
    await api(`/api/peers/${id}/${action}/`, {
      method: "POST",
      body: JSON.stringify({ reason: String(form.get("reason") ?? "") }),
    });
  } catch (e) {
    return { error: e instanceof ApiError ? e.message : "Request failed" };
  }
  revalidatePath("/nodes");
  return {};
}

export async function createPeer(_: ActionState, form: FormData): Promise<ActionState> {
  const name = String(form.get("name") ?? "").trim();
  const publicKey = String(form.get("public_key") ?? "").trim();
  try {
    const res = await api<{ peer: Peer; client_config: string | null }>("/api/peers/", {
      method: "POST",
      body: JSON.stringify({ name, public_key: publicKey }),
    });
    revalidatePath("/nodes");
    return { name: res.peer.name, config: res.client_config ?? undefined };
  } catch (e) {
    if (e instanceof ApiError) {
      const first = Object.values(e.body)[0];
      return { error: Array.isArray(first) ? String(first[0]) : e.message };
    }
    return { error: "Request failed" };
  }
}
