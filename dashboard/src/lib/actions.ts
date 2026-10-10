"use server";

import { revalidatePath } from "next/cache";

import type { FormState } from "@/components/action-form";

import { ApiError, api } from "./orchestrator";

const id = (form: FormData) => {
  const n = Number(form.get("id"));
  if (!Number.isInteger(n) || n <= 0) throw new ApiError(400, { detail: "bad id" });
  return n;
};

async function call(path: string, body: unknown, revalidate: string[]): Promise<FormState> {
  try {
    await api(path, { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    if (e instanceof ApiError) {
      const first = Object.values(e.body)[0];
      return { error: Array.isArray(first) ? String(first[0]) : e.message };
    }
    throw e;
  }
  revalidate.forEach((p) => revalidatePath(p, "page"));
  return { ok: true };
}

export async function setAllowlist(_: FormState, form: FormData): Promise<FormState> {
  const peer = id(form);
  return call(
    `/api/peers/${peer}/allowlist/`,
    { allowlisted: form.get("allowlisted") === "true" },
    [`/nodes/${peer}`, "/nodes"],
  );
}

const INCIDENT_ACTIONS = new Set(["acknowledge", "resolve"]);

export async function incidentAction(_: FormState, form: FormData): Promise<FormState> {
  const incident = id(form);
  const action = String(form.get("op"));
  if (!INCIDENT_ACTIONS.has(action)) return { error: "bad action" };
  return call(
    `/api/incidents/${incident}/${action}/`,
    { note: String(form.get("note") ?? "") },
    [`/incidents/${incident}`, "/incidents"],
  );
}

const STAGES = new Set(["shadow", "active"]);

export async function deployModel(_: FormState, form: FormData): Promise<FormState> {
  const stage = String(form.get("stage"));
  if (!STAGES.has(stage)) return { error: "bad stage" };
  return call(`/api/models/${id(form)}/deploy/`, { stage }, ["/models"]);
}

export async function undeployModel(): Promise<FormState> {
  return call("/api/models/undeploy/", {}, ["/models"]);
}

export async function saveEnforcement(_: FormState, form: FormData): Promise<FormState> {
  const num = (k: string) => Number(form.get(k));
  try {
    await api("/api/settings/enforcement", {
      method: "PATCH",
      body: JSON.stringify({
        mode: form.get("mode"),
        incident_below: num("incident_below"),
        quarantine_below: num("quarantine_below"),
        revoke_below: num("revoke_below"),
        release_above: num("release_above"),
        min_quarantine_minutes: num("min_quarantine_minutes"),
        decay_minutes: num("decay_minutes"),
        auto_release: form.get("auto_release") === "on",
      }),
    });
  } catch (e) {
    if (e instanceof ApiError) {
      const first = Object.values(e.body)[0];
      return { error: Array.isArray(first) ? String(first[0]) : e.message };
    }
    throw e;
  }
  revalidatePath("/settings");
  return { ok: true };
}
