"use server";

import { revalidatePath } from "next/cache";

import type { FormState } from "@/components/action-form";
import { getT } from "@/i18n/server";

import { api, errorMessage } from "./orchestrator";

const id = (form: FormData) => {
  const n = Number(form.get("id"));
  return Number.isInteger(n) && n > 0 ? n : null;
};

async function call(
  path: string,
  init: { method: string; body?: unknown },
  revalidate: string[],
): Promise<FormState> {
  const t = await getT();
  try {
    await api(path, {
      method: init.method,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch (e) {
    return { error: errorMessage(e, t("error.requestFailed")) };
  }
  revalidate.forEach((p) => revalidatePath(p, "page"));
  return { ok: true };
}

const post = (path: string, body: unknown, revalidate: string[]) =>
  call(path, { method: "POST", body }, revalidate);

async function badRequest(): Promise<FormState> {
  return { error: (await getT())("error.badRequest") };
}

// --- peers -------------------------------------------------------------------------------

export async function setAllowlist(_: FormState, form: FormData): Promise<FormState> {
  const peer = id(form);
  if (!peer) return badRequest();
  return post(
    `/api/peers/${peer}/allowlist/`,
    { allowlisted: form.get("allowlisted") === "true" },
    [`/nodes/${peer}`, "/nodes"],
  );
}

// --- incidents ---------------------------------------------------------------------------

const INCIDENT_ACTIONS = new Set(["acknowledge", "resolve"]);

export async function incidentAction(_: FormState, form: FormData): Promise<FormState> {
  const incident = id(form);
  const action = String(form.get("op"));
  if (!incident || !INCIDENT_ACTIONS.has(action)) return badRequest();
  return post(
    `/api/incidents/${incident}/${action}/`,
    { note: String(form.get("note") ?? "") },
    [`/incidents/${incident}`, "/incidents"],
  );
}

// --- models ------------------------------------------------------------------------------

const STAGES = new Set(["shadow", "active", "available"]);

export async function deployModel(_: FormState, form: FormData): Promise<FormState> {
  const stage = String(form.get("stage"));
  const model = id(form);
  if (!model || !STAGES.has(stage)) return badRequest();
  return post(`/api/models/${model}/deploy/`, { stage }, ["/models"]);
}

export async function undeployModel(): Promise<FormState> {
  return post("/api/models/undeploy/", {}, ["/models"]);
}

export async function retrainModel(): Promise<FormState> {
  return post("/api/models/retrain/", {}, ["/models"]);
}

export async function saveMLOps(_: FormState, form: FormData): Promise<FormState> {
  const num = (k: string) => Number(form.get(k));
  return call(
    "/api/settings/mlops",
    {
      method: "PATCH",
      body: {
        auto_retrain: form.get("auto_retrain") === "on",
        retrain_interval_hours: num("retrain_interval_hours"),
        drift_threshold: num("drift_threshold"),
        model_kind: form.get("model_kind"),
        train_real_minutes: num("train_real_minutes"),
        auto_shadow: form.get("auto_shadow") === "on",
        auto_promote: form.get("auto_promote") === "on",
        promote_min_windows: num("promote_min_windows"),
        promote_max_rate: num("promote_max_rate") / 100,
      },
    },
    ["/models"],
  );
}

// --- enforcement -------------------------------------------------------------------------

export async function saveEnforcement(_: FormState, form: FormData): Promise<FormState> {
  const num = (k: string) => Number(form.get(k));
  return call(
    "/api/settings/enforcement",
    {
      method: "PATCH",
      body: {
        mode: form.get("mode"),
        incident_below: num("incident_below"),
        quarantine_below: num("quarantine_below"),
        revoke_below: num("revoke_below"),
        release_above: num("release_above"),
        min_quarantine_minutes: num("min_quarantine_minutes"),
        decay_minutes: num("decay_minutes"),
        auto_release: form.get("auto_release") === "on",
      },
    },
    ["/settings"],
  );
}

// --- access: resources and groups --------------------------------------------------------

function resourceBody(form: FormData) {
  return {
    name: String(form.get("name") ?? "").trim(),
    description: String(form.get("description") ?? "").trim(),
    path_prefix: String(form.get("path_prefix") ?? "").trim(),
    upstream: String(form.get("upstream") ?? "").trim(),
    min_trust: Number(form.get("min_trust")),
    groups: form.getAll("groups").map(String),
    enabled: form.get("enabled") === "on",
  };
}

export async function createResource(_: FormState, form: FormData): Promise<FormState> {
  return post("/api/resources/", resourceBody(form), ["/access"]);
}

export async function updateResource(_: FormState, form: FormData): Promise<FormState> {
  const res = id(form);
  if (!res) return badRequest();
  const { min_trust, groups, enabled } = resourceBody(form);
  return call(
    `/api/resources/${res}/`,
    { method: "PATCH", body: { min_trust, groups, enabled } },
    ["/access"],
  );
}

export async function deleteResource(_: FormState, form: FormData): Promise<FormState> {
  const res = id(form);
  if (!res) return badRequest();
  return call(`/api/resources/${res}/`, { method: "DELETE" }, ["/access"]);
}

export async function createGroup(_: FormState, form: FormData): Promise<FormState> {
  return post(
    "/api/groups/",
    {
      name: String(form.get("name") ?? "").trim(),
      description: String(form.get("description") ?? "").trim(),
    },
    ["/access"],
  );
}

export async function deleteGroup(_: FormState, form: FormData): Promise<FormState> {
  const group = id(form);
  if (!group) return badRequest();
  return call(`/api/groups/${group}/`, { method: "DELETE" }, ["/access"]);
}

// --- gateways ----------------------------------------------------------------------------

export type TokenState = FormState & { token?: string; name?: string };

export async function createGateway(_: TokenState, form: FormData): Promise<TokenState> {
  const t = await getT();
  const name = String(form.get("name") ?? "").trim();
  try {
    const res = await api<{ token: string }>("/api/gateways/", {
      method: "POST",
      body: JSON.stringify({
        name,
        endpoint: String(form.get("endpoint") ?? "").trim(),
        wg_subnet: String(form.get("wg_subnet") ?? "").trim(),
      }),
    });
    revalidatePath("/gateways", "page");
    return { ok: true, token: res.token, name };
  } catch (e) {
    return { error: errorMessage(e, t("error.requestFailed")) };
  }
}

export async function rotateGatewayToken(_: TokenState, form: FormData): Promise<TokenState> {
  const t = await getT();
  const gw = id(form);
  if (!gw) return badRequest();
  try {
    const res = await api<{ token: string }>(`/api/gateways/${gw}/rotate-token/`, {
      method: "POST",
    });
    return { ok: true, token: res.token, name: String(form.get("name") ?? "") };
  } catch (e) {
    return { error: errorMessage(e, t("error.requestFailed")) };
  }
}

export async function deleteGateway(_: FormState, form: FormData): Promise<FormState> {
  const gw = id(form);
  if (!gw) return badRequest();
  return call(`/api/gateways/${gw}/`, { method: "DELETE" }, ["/gateways"]);
}

// --- notifications -----------------------------------------------------------------------

export async function createChannel(_: FormState, form: FormData): Promise<FormState> {
  return post(
    "/api/notifications/channels/",
    {
      name: String(form.get("name") ?? "").trim(),
      kind: form.get("kind"),
      url: String(form.get("url") ?? "").trim(),
      secret: String(form.get("secret") ?? ""),
      telegram_chat_id: String(form.get("telegram_chat_id") ?? "").trim(),
      events: form.getAll("events").map(String),
      min_severity: form.get("min_severity"),
      enabled: true,
    },
    ["/notifications"],
  );
}

export async function toggleChannel(_: FormState, form: FormData): Promise<FormState> {
  const ch = id(form);
  if (!ch) return badRequest();
  return call(
    `/api/notifications/channels/${ch}/`,
    { method: "PATCH", body: { enabled: form.get("enabled") === "true" } },
    ["/notifications"],
  );
}

export async function testChannel(_: FormState, form: FormData): Promise<FormState> {
  const ch = id(form);
  if (!ch) return badRequest();
  return post(`/api/notifications/channels/${ch}/test/`, {}, ["/notifications"]);
}

export async function deleteChannel(_: FormState, form: FormData): Promise<FormState> {
  const ch = id(form);
  if (!ch) return badRequest();
  return call(`/api/notifications/channels/${ch}/`, { method: "DELETE" }, ["/notifications"]);
}
