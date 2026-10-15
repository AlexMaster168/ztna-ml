import type { Gateway } from "./orchestrator";

/** A gateway that is behind but checked in recently is applying changes right now. */
export function gatewayState(g: Gateway): string {
  if (g.in_sync) return "ok";
  const seen = g.last_seen_at ? (Date.now() - new Date(g.last_seen_at).getTime()) / 1000 : Infinity;
  return seen < 30 ? "syncing" : "degraded";
}
