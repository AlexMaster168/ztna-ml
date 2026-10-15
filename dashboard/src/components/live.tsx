"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useT } from "@/i18n/client";
import type { T } from "@/i18n";
import { reasonText } from "@/lib/localize";

import { clock } from "./format";

export type LiveEvent = {
  type: "anomaly" | "peer.status" | "peer.score" | "peer.access" | string;
  ts: number;
  peer_id?: number;
  name?: string;
  kind?: string;
  source?: string;
  penalty?: number;
  status?: string;
  reason?: string;
  actor?: string;
  score?: number;
  incident_id?: number;
};

/** Subscribes to /api/live (SSE). EventSource reconnects on its own. */
export function useLive(onEvent: (e: LiveEvent) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const es = new EventSource("/api/live");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (m) => {
      try {
        handler.current(JSON.parse(m.data) as LiveEvent);
      } catch {
        /* ignore malformed */
      }
    };
    return () => es.close();
  }, []);
  return connected;
}

/** Re-renders the server components of the current page when relevant events arrive. */
export function LiveRefresh({ types, peerId }: { types: string[]; peerId?: number }) {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useLive((e) => {
    if (!types.includes(e.type)) return;
    if (peerId !== undefined && e.peer_id !== peerId) return;
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      router.refresh();
    }, 1000);
  });
  return null;
}

function describe(t: T, e: LiveEvent): string {
  switch (e.type) {
    case "anomaly":
      return `${t.v("source", e.source)}: ${t.v("kind", e.kind)} (−${e.penalty})`;
    case "peer.status":
      return t("live.status", {
        status: t.v("peerStatus", e.status),
        reason: e.reason ? ` — ${reasonText(t, e.reason)}` : "",
        actor: e.actor ?? "—",
      });
    case "peer.access":
      return t("live.access", { score: Math.round(e.score ?? 0) });
    default:
      return t.v("liveType", e.type);
  }
}

export function LiveFeed() {
  const t = useT();
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const connected = useLive((e) => {
    if (e.type === "peer.score") return; // too chatty for a feed
    setEvents((prev) => [e, ...prev].slice(0, 25));
  });
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{t("live.title")}</h2>
        <span className="flex items-center gap-1.5 text-xs text-zinc-500">
          <span
            aria-hidden
            className={`inline-block size-2 rounded-full ${connected ? "bg-emerald-500" : "bg-zinc-400"}`}
          />
          {connected ? t("live.connected") : t("live.reconnecting")}
        </span>
      </div>
      {events.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">{t("live.waiting")}</p>
      ) : (
        <ul className="mt-3 divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
          {events.map((e, i) => (
            <li key={`${e.ts}-${i}`} className="flex flex-wrap gap-x-3 py-1.5">
              <span className="tabular-nums text-zinc-500">
                {clock(e.ts)}
              </span>
              {e.peer_id ? (
                <Link href={`/nodes/${e.peer_id}`} className="font-medium hover:underline">
                  {e.name ?? `#${e.peer_id}`}
                </Link>
              ) : null}
              <span className="text-zinc-700 dark:text-zinc-300">{describe(t, e)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
