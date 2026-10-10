import { cookies } from "next/headers";
import { createClient } from "redis";

import { ORCHESTRATOR_URL } from "@/lib/orchestrator";
import { ACCESS_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379/0";
const CHANNEL = "ztna:live";

/** Server-Sent Events bridge from the orchestrator's Redis pub/sub channel. */
export async function GET(req: Request) {
  const token = (await cookies()).get(ACCESS_COOKIE)?.value;
  const me = token
    ? await fetch(`${ORCHESTRATOR_URL}/api/me`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      }).catch(() => null)
    : null;
  if (!me?.ok) return new Response("unauthorized", { status: 401 });

  const sub = createClient({ url: REDIS_URL });
  const encoder = new TextEncoder();
  let keepAlive: ReturnType<typeof setInterval> | undefined;

  const close = async () => {
    clearInterval(keepAlive);
    if (sub.isOpen) await sub.quit().catch(() => undefined);
  };

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (chunk: string) => {
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch {
          void close();
        }
      };
      sub.on("error", () => undefined);
      await sub.connect();
      await sub.subscribe(CHANNEL, (message) => send(`data: ${message}\n\n`));
      send("retry: 3000\n: connected\n\n");
      keepAlive = setInterval(() => send(": ping\n\n"), 15_000);
      req.signal.addEventListener("abort", () => {
        void close();
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      });
    },
    cancel: close,
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
