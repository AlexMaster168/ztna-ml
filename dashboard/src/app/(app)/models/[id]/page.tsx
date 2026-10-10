import Link from "next/link";
import { notFound } from "next/navigation";

import { StatusBadge } from "@/components/status-badge";
import { ApiError, api, type ModelVersion } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export default async function ModelPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let m: ModelVersion;
  try {
    m = await api<ModelVersion>(`/api/models/${Number(id)}/`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const perKind = Object.entries(m.metrics.per_kind ?? {});
  return (
    <div className="space-y-4">
      <Link href="/models" className="text-sm text-zinc-500 hover:underline">
        ← Models
      </Link>
      <h1 className="flex flex-wrap items-center gap-3 text-xl font-semibold">
        <span className="font-mono">{m.version}</span> <StatusBadge status={m.stage} />
      </h1>
      <div className="card overflow-x-auto">
        <h2 className="px-4 pt-4 text-sm font-medium">Recall by attack type (held-out simulation)</h2>
        <table className="mt-2 w-full min-w-[560px] text-sm">
          <thead className="text-left text-zinc-500">
            <tr>
              <th className="px-4 py-1.5 font-medium">Attack</th>
              <th className="px-4 py-1.5 text-right font-medium">Windows</th>
              <th className="px-4 py-1.5 text-right font-medium">Rules</th>
              <th className="px-4 py-1.5 text-right font-medium">Model</th>
              <th className="px-4 py-1.5 text-right font-medium">Rules + model</th>
            </tr>
          </thead>
          <tbody>
            {perKind.map(([kind, s]) => (
              <tr key={kind} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="px-4 py-1.5">{kind}</td>
                <td className="px-4 py-1.5 text-right tabular-nums">{s.windows}</td>
                <td className="px-4 py-1.5 text-right tabular-nums">{pct(s.rules_recall)}</td>
                <td className="px-4 py-1.5 text-right tabular-nums">{pct(s.model_recall)}</td>
                <td className="px-4 py-1.5 text-right tabular-nums font-medium">
                  {pct(s.combined_recall)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="card p-4" open>
        <summary className="cursor-pointer text-sm font-medium">Training report</summary>
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs">{m.report_md}</pre>
      </details>
    </div>
  );
}
