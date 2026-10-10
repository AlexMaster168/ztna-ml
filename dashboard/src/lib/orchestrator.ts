import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ACCESS_COOKIE } from "./session";

export const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";

export type Health = {
  status: "ok" | "degraded" | "unreachable";
  db?: string;
  redis?: string;
};

export type Role = "viewer" | "analyst" | "admin";
export type Me = { id: number; username: string; role: Role };

export type PeerStatus = "active" | "quarantined" | "revoked";
export type Peer = {
  id: number;
  name: string;
  gateway: string;
  owner: string | null;
  public_key: string;
  wg_ip: string;
  status: PeerStatus;
  status_reason: string;
  trust_score: number;
  allowlisted: boolean;
  quarantined_by_system: boolean;
  created_at: string;
  last_handshake_at: string | null;
  rx_bytes: number;
  tx_bytes: number;
};

export type Gateway = {
  id: number;
  name: string;
  endpoint: string;
  wg_subnet: string;
  public_key: string;
  revision: number;
  applied_revision: number;
  in_sync: boolean;
  last_seen_at: string | null;
};

export type AuditEntry = {
  id: number;
  created_at: string;
  actor_label: string;
  action: string;
  target_type: string;
  target_id: string;
  data: Record<string, unknown>;
  source_ip: string | null;
};

export type Page<T> = { count: number; results: T[] };

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: Record<string, unknown>,
  ) {
    super(
      typeof body.detail === "string" ? body.detail : `orchestrator returned ${status}`,
    );
  }
}

/** Authenticated call to the orchestrator using the session's access token. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = (await cookies()).get(ACCESS_COOKIE)?.value;
  if (!token) redirect("/login");
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });
  if (res.status === 401) redirect("/login");
  if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => ({})));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export async function getHealth(): Promise<Health> {
  try {
    const res = await fetch(`${ORCHESTRATOR_URL}/healthz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2000),
    });
    return (await res.json()) as Health;
  } catch {
    return { status: "unreachable" };
  }
}

const RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };
export const can = (me: Me, role: Role) => RANK[me.role] >= RANK[role];

export type AnomalyEvent = {
  id: number;
  peer: number | null;
  peer_name: string | null;
  peer_ip: string;
  source: "rule" | "model";
  kind: string;
  penalty: number;
  details: Record<string, unknown>;
  window_start: string;
  created_at: string;
  incident: number | null;
};

export type IncidentStatus = "open" | "acknowledged" | "resolved";
export type Incident = {
  id: number;
  peer: number;
  peer_name: string;
  status: IncidentStatus;
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  min_trust_score: number;
  actions: { action: string; at: string; reason: string }[];
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_note: string;
  event_count: number;
  events?: AnomalyEvent[];
};

type Metrics = { fpr: number; precision: number; recall: number; f1: number; roc_auc: number };
export type ModelVersion = {
  id: number;
  version: string;
  kind: string;
  threshold: number;
  metrics: {
    model?: Metrics;
    baseline?: Metrics;
    per_kind?: Record<
      string,
      { windows: number; rules_recall: number; model_recall: number; combined_recall: number }
    >;
    train_windows?: number;
    live_holdout?: {
      normal_windows: number | null;
      normal_fpr: number | null;
      attack_windows: number | null;
      attack_recall: number | null;
    };
  };
  trained_at: string | null;
  stage: "available" | "shadow" | "active";
  stage_changed_at: string | null;
  missing: boolean;
  report_md?: string;
};

export type EnforcementSettings = {
  mode: "monitor" | "auto_quarantine" | "auto_revoke";
  incident_below: number;
  quarantine_below: number;
  revoke_below: number;
  release_above: number;
  auto_release: boolean;
  min_quarantine_minutes: number;
  decay_minutes: number;
  updated_at: string;
};

export type WindowSnapshot = {
  window_start: number;
  requests: number;
  unique_paths: number;
  status_4xx: number;
  status_404: number;
  auth_failures: number;
  bytes_out: number;
  user_agents: string[];
  anomaly_ratio: number | null;
};

export type RecentRequest = {
  ts: number;
  peer_ip: string;
  method: string;
  path: string;
  status: number;
  bytes_sent: number;
  ua: string;
};

export type PeerMetrics = { history: WindowSnapshot[]; recent_requests: RecentRequest[] };
