import { ActionForm } from "@/components/action-form";
import { formatTime } from "@/components/format";
import { getT } from "@/i18n/server";
import { saveEnforcement } from "@/lib/actions";
import { api, can, type EnforcementSettings, type Me } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const MODES = ["monitor", "auto_quarantine", "auto_revoke"] as const;

export default async function SettingsPage() {
  const [t, me, cfg] = await Promise.all([
    getT(),
    api<Me>("/api/me"),
    api<EnforcementSettings>("/api/settings/enforcement"),
  ]);
  const admin = can(me, "admin");
  const field = (name: keyof EnforcementSettings, label: string, step = "1") => (
    <label className="block text-sm">
      {label}
      <input
        name={name}
        type="number"
        step={step}
        min="0"
        defaultValue={String(cfg[name])}
        className="input mt-1"
        disabled={!admin}
        required
      />
    </label>
  );

  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-semibold">{t("settings.title")}</h1>
      <p className="text-sm text-zinc-500">
        {t("settings.formula")} Σ·e<sup>−Δt/τ</sup>.{" "}
        {t("settings.changed", { at: formatTime(cfg.updated_at, t.locale) })}
      </p>
      <ActionForm action={saveEnforcement} className="card space-y-4 p-4">
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("settings.mode")}</legend>
          {MODES.map((m) => (
            <label key={m} className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                value={m}
                defaultChecked={cfg.mode === m}
                disabled={!admin}
                className="mt-1"
              />
              <span>
                {t.v("mode", m)} <span className="text-zinc-500">— {t.v("modeHint", m)}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="grid gap-4 sm:grid-cols-3">
          {field("incident_below", t("settings.incidentBelow"))}
          {field("quarantine_below", t("settings.quarantineBelow"))}
          {field("revoke_below", t("settings.revokeBelow"))}
          {field("release_above", t("settings.releaseAbove"))}
          {field("min_quarantine_minutes", t("settings.minQuarantine"))}
          {field("decay_minutes", t("settings.decay"), "0.5")}
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="auto_release" defaultChecked={cfg.auto_release} disabled={!admin} />
          {t("settings.autoRelease")}
        </label>
        {admin ? (
          <button className="btn btn-primary">{t("common.save")}</button>
        ) : (
          <p className="text-sm text-zinc-500">{t("settings.adminOnly")}</p>
        )}
      </ActionForm>
    </div>
  );
}
