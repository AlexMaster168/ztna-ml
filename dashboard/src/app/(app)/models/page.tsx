import Link from "next/link";

import { ActionForm } from "@/components/action-form";
import { formatTime, pct } from "@/components/format";
import { StatusBadge } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { deployModel, retrainModel, saveMLOps } from "@/lib/actions";
import {
  api,
  can,
  type LiveModelStats,
  type LiveStatus,
  type Me,
  type MLOpsSettings,
  type ModelVersion,
  type Page,
} from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const unix = (v?: string) => (v ? new Date(Number(v) * 1000).toISOString() : null);

export default async function ModelsPage() {
  const [t, me, models, live, cfg] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<Page<ModelVersion>>("/api/models/?limit=50"),
    api<LiveStatus>("/api/models/live/"),
    api<MLOpsSettings>("/api/settings/mlops"),
  ]);
  const admin = can(me, "admin");
  const byVersion = new Map(models.results.map((m) => [m.version, m]));
  const trainer = live.trainer ?? {};
  const drift = live.drift;

  const slot = (stage: "active" | "shadow", stats: LiveModelStats | null) => {
    const m = stats ? byVersion.get(stats.version) : models.results.find((x) => x.stage === stage);
    return (
      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={stage} label={t.v("stage", stage)} />
          <h2 className="font-medium">{stage === "active" ? t("models.champion") : t("models.challenger")}</h2>
        </div>
        {m ? (
          <>
            <Link href={`/models/${m.id}`} className="mt-2 block truncate font-mono text-sm hover:underline">
              {m.version}
            </Link>
            <dl className="mt-3 grid grid-cols-2 gap-1 text-sm">
              <dt className="text-zinc-500">{t("models.liveWindows")}</dt>
              <dd className="tabular-nums">{stats?.windows ?? 0}</dd>
              <dt className="text-zinc-500">{t("models.liveRate")}</dt>
              <dd className="tabular-nums">{pct(stats?.anomaly_rate)}</dd>
              <dt className="text-zinc-500">{t("models.disagreements")}</dt>
              <dd className="tabular-nums">{stats?.disagreements ?? 0}</dd>
              <dt className="text-zinc-500">{t("models.offline")}</dt>
              <dd className="tabular-nums">
                {t("models.recallFpr", {
                  recall: pct(m.metrics.model?.recall),
                  fpr: pct(m.metrics.model?.fpr),
                })}
              </dd>
            </dl>
            {admin && (
              <div className="mt-3 flex flex-wrap gap-2">
                {stage === "shadow" && (
                  <ActionForm action={deployModel}>
                    <input type="hidden" name="id" value={m.id} />
                    <input type="hidden" name="stage" value="active" />
                    <button className="btn btn-primary py-1">{t("models.promote")}</button>
                  </ActionForm>
                )}
                <ActionForm action={deployModel}>
                  <input type="hidden" name="id" value={m.id} />
                  <input type="hidden" name="stage" value="available" />
                  <button className="btn btn-secondary py-1">{t("models.retire")}</button>
                </ActionForm>
              </div>
            )}
          </>
        ) : (
          <p className="mt-2 text-sm text-zinc-500">
            {stage === "active" ? t("models.noActive") : t("models.noChallenger")}
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("models.title")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-500">{t("models.intro")}</p>
      </div>

      <section className="grid gap-4 lg:grid-cols-2">
        {slot("active", live.active)}
        {slot("shadow", live.shadow)}
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="card p-4">
          <h2 className="font-medium">{t("models.drift")}</h2>
          {drift ? (
            drift.status === "ok" ? (
              <>
                <p className="mt-2 text-3xl font-semibold tabular-nums">
                  <span
                    className={
                      (drift.score ?? 0) >= cfg.drift_threshold
                        ? "text-amber-600 dark:text-amber-400"
                        : ""
                    }
                  >
                    {drift.score?.toFixed(2)}
                  </span>
                  <span className="ml-2 text-sm font-normal text-zinc-500">
                    {t("models.driftThreshold", { v: cfg.drift_threshold })}
                  </span>
                </p>
                <ul className="mt-2 space-y-0.5 text-xs">
                  {Object.entries(drift.top_features ?? {}).map(([f, v]) => (
                    <li key={f} className="flex justify-between gap-2">
                      <span>{t.v("feature", f)}</span>
                      <span className="tabular-nums">{v.toFixed(2)} σ</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="mt-2 text-sm text-zinc-500">
                {t.v("driftStatus", drift.status)} ({t("models.windows", { n: drift.windows })})
              </p>
            )
          ) : (
            <p className="mt-2 text-sm text-zinc-500">{t("models.noDrift")}</p>
          )}
          <p className="mt-2 text-xs text-zinc-500">{t("models.driftHint")}</p>
        </div>

        <div className="card p-4">
          <div className="flex items-center gap-2">
            <h2 className="font-medium">{t("models.trainer")}</h2>
            <StatusBadge
              status={trainer.state === "training" ? "training" : "available"}
              label={t.v("trainerState", trainer.state || "idle")}
            />
          </div>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-zinc-500">{t("models.lastRun")}</dt>
            <dd>
              {trainer.last_finished_at
                ? `${formatTime(unix(trainer.last_finished_at)!, t.locale)} · ${t.v("trigger", trainer.last_trigger)}`
                : "—"}
            </dd>
            <dt className="text-zinc-500">{t("models.result")}</dt>
            <dd>
              {trainer.last_finished_at ? (
                <>
                  <StatusBadge
                    status={trainer.last_passed === "1" ? "allowed" : "failed"}
                    label={trainer.last_passed === "1" ? t("models.passed") : t("models.rejected")}
                  />{" "}
                  <span className="text-xs text-zinc-500">{trainer.last_reason}</span>
                </>
              ) : (
                "—"
              )}
            </dd>
            <dt className="text-zinc-500">{t("models.nextRun")}</dt>
            <dd>
              {cfg.auto_retrain && trainer.next_scheduled_at
                ? formatTime(unix(trainer.next_scheduled_at)!, t.locale)
                : t("models.manualOnly")}
            </dd>
          </dl>
          {admin && (
            <ActionForm action={retrainModel} className="mt-3">
              <button className="btn btn-primary py-1" disabled={trainer.state === "training"}>
                {t("models.retrainNow")}
              </button>
            </ActionForm>
          )}
        </div>

        <div className="card p-4">
          <h2 className="font-medium">{t("models.workers")}</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {live.workers.map((w) => (
              <li key={w.name} className="flex flex-wrap justify-between gap-2">
                <span className="font-mono text-xs">{w.name}</span>
                <span className="text-xs text-zinc-500">
                  {t("models.shards", { list: w.shards.join(", ") || "—" })}
                </span>
              </li>
            ))}
            {live.workers.length === 0 && <li className="text-zinc-500">{t("models.noWorkers")}</li>}
          </ul>
          <p className="mt-2 text-xs text-zinc-500">{t("models.workersHint")}</p>
        </div>
      </section>

      {admin && (
        <ActionForm action={saveMLOps} className="card grid gap-4 p-4 sm:grid-cols-3">
          <h2 className="font-medium sm:col-span-3">{t("models.settings")}</h2>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="auto_retrain" defaultChecked={cfg.auto_retrain} />
            {t("models.autoRetrain")}
          </label>
          <label className="block text-sm">
            {t("models.interval")}
            <input name="retrain_interval_hours" type="number" min="1" defaultValue={cfg.retrain_interval_hours} className="input mt-1" />
          </label>
          <label className="block text-sm">
            {t("models.driftThresholdField")}
            <input name="drift_threshold" type="number" step="0.05" min="0" defaultValue={cfg.drift_threshold} className="input mt-1" />
          </label>
          <label className="block text-sm">
            {t("models.kind")}
            <select name="model_kind" defaultValue={cfg.model_kind} className="input mt-1">
              <option value="lstm">LSTM-AE</option>
              <option value="dense">Dense-AE</option>
            </select>
          </label>
          <label className="block text-sm">
            {t("models.realMinutes")}
            <input name="train_real_minutes" type="number" min="10" defaultValue={cfg.train_real_minutes} className="input mt-1" />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="auto_shadow" defaultChecked={cfg.auto_shadow} />
            {t("models.autoShadow")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="auto_promote" defaultChecked={cfg.auto_promote} />
            {t("models.autoPromote")}
          </label>
          <label className="block text-sm">
            {t("models.promoteWindows")}
            <input name="promote_min_windows" type="number" min="50" defaultValue={cfg.promote_min_windows} className="input mt-1" />
          </label>
          <label className="block text-sm">
            {t("models.promoteRate")}
            <input name="promote_max_rate" type="number" step="0.5" min="0.5" max="100" defaultValue={cfg.promote_max_rate * 100} className="input mt-1" />
          </label>
          <div className="sm:col-span-3">
            <button className="btn btn-primary">{t("common.save")}</button>
          </div>
        </ActionForm>
      )}

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 font-medium">{t("models.registry")}</h2>
        <table className="mt-2 w-full min-w-[820px] text-sm">
          <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">{t("models.version")}</th>
              <th className="px-4 py-2 font-medium">{t("models.stage")}</th>
              <th className="px-4 py-2 text-right font-medium">Recall</th>
              <th className="px-4 py-2 text-right font-medium">FPR</th>
              <th className="px-4 py-2 text-right font-medium">ROC-AUC</th>
              <th className="px-4 py-2 text-right font-medium">{t("models.baselineRecall")}</th>
              <th className="px-4 py-2 text-right font-medium">{t("models.realFpr")}</th>
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
                    {m.kind} · {m.trained_at ? formatTime(m.trained_at, t.locale) : "—"}
                    {m.missing && ` · ${t("models.missing")}`}
                  </div>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={m.stage} label={t.v("stage", m.stage)} />
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
                  {pct(m.metrics.live_holdout?.normal_fpr)}
                </td>
                <td className="px-4 py-2">
                  {admin && !m.missing && m.stage === "available" && (
                    <div className="flex justify-end gap-2">
                      <ActionForm action={deployModel}>
                        <input type="hidden" name="id" value={m.id} />
                        <input type="hidden" name="stage" value="shadow" />
                        <button className="btn btn-secondary py-1">{t("models.toShadow")}</button>
                      </ActionForm>
                      <ActionForm action={deployModel}>
                        <input type="hidden" name="id" value={m.id} />
                        <input type="hidden" name="stage" value="active" />
                        <button className="btn btn-primary py-1">{t("models.activate")}</button>
                      </ActionForm>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {models.results.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-zinc-500">
                  {t("models.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
