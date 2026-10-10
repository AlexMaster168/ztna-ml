import Link from "next/link";

import { ActionForm } from "@/components/action-form";
import { formatTime } from "@/components/format";
import { StatusBadge } from "@/components/status-badge";
import { deployModel, undeployModel } from "@/lib/actions";
import { api, can, type Me, type ModelVersion, type Page } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const pct = (v?: number) => (v === undefined || v === null ? "—" : `${(v * 100).toFixed(1)}%`);

export default async function ModelsPage() {
  const [me, models] = await Promise.all([
    api<Me>("/api/me"),
    api<Page<ModelVersion>>("/api/models/?limit=50"),
  ]);
  const admin = can(me, "admin");
  const deployed = models.results.find((m) => m.stage !== "available");

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Models</h1>
      <div className="card flex flex-wrap items-center gap-3 p-4 text-sm">
        {deployed ? (
          <>
            <span>
              Deployed: <span className="font-mono">{deployed.version}</span>
            </span>
            <StatusBadge status={deployed.stage} />
            <span className="text-zinc-500">
              {deployed.stage === "shadow"
                ? "scores traffic, detections are logged but do not affect trust"
                : "detections lower trust scores"}
            </span>
            {admin && (
              <ActionForm action={undeployModel}>
                <button className="btn btn-secondary">Undeploy</button>
              </ActionForm>
            )}
          </>
        ) : (
          <span className="text-zinc-500">No model deployed: rules only.</span>
        )}
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">Version</th>
              <th className="px-4 py-2 font-medium">Stage</th>
              <th className="px-4 py-2 text-right font-medium">Recall</th>
              <th className="px-4 py-2 text-right font-medium">FPR</th>
              <th className="px-4 py-2 text-right font-medium">ROC-AUC</th>
              <th className="px-4 py-2 text-right font-medium">Baseline recall</th>
              <th className="px-4 py-2 text-right font-medium">Real-traffic FPR</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {models.results.map((m) => (
              <tr key={m.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                <td className="px-4 py-2">
                  <Link href={`/models/${m.id}`} className="font-mono hover:underline">
                    {m.version}
                  </Link>
                  <div className="text-xs text-zinc-500">
                    {m.kind} · {m.trained_at ? formatTime(m.trained_at) : "—"}
                    {m.missing && " · artifact missing"}
                  </div>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={m.stage} />
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{pct(m.metrics.model?.recall)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{pct(m.metrics.model?.fpr)}</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {m.metrics.model?.roc_auc?.toFixed(3) ?? "—"}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-zinc-500">
                  {pct(m.metrics.baseline?.recall)}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {pct(m.metrics.live_holdout?.normal_fpr ?? undefined)}
                </td>
                <td className="px-4 py-2">
                  {admin && !m.missing && (
                    <div className="flex justify-end gap-2">
                      {m.stage !== "shadow" && (
                        <ActionForm action={deployModel}>
                          <input type="hidden" name="id" value={m.id} />
                          <input type="hidden" name="stage" value="shadow" />
                          <button className="btn btn-secondary">Shadow</button>
                        </ActionForm>
                      )}
                      {m.stage !== "active" && (
                        <ActionForm action={deployModel}>
                          <input type="hidden" name="id" value={m.id} />
                          <input type="hidden" name="stage" value="active" />
                          <button className="btn btn-primary">Activate</button>
                        </ActionForm>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {models.results.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-zinc-500">
                  No trained models. Run <code>docker compose --profile train run --rm ml-train</code>.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
