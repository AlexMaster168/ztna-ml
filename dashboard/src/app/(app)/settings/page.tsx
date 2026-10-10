import { ActionForm } from "@/components/action-form";
import { formatTime } from "@/components/format";
import { saveEnforcement } from "@/lib/actions";
import { api, can, type EnforcementSettings, type Me } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const MODES = [
  { value: "monitor", label: "Monitor only", hint: "score and open incidents, never isolate" },
  { value: "auto_quarantine", label: "Auto-quarantine", hint: "isolate below the quarantine threshold" },
  { value: "auto_revoke", label: "Auto-quarantine and revoke", hint: "also revoke below the revoke threshold" },
];

export default async function SettingsPage() {
  const [me, cfg] = await Promise.all([
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
      <h1 className="text-2xl font-semibold">Enforcement</h1>
      <p className="text-sm text-zinc-500">
        Trust = 100 − Σ penalty·e<sup>−Δt/τ</sup>. Last changed {formatTime(cfg.updated_at)}.
      </p>
      <ActionForm action={saveEnforcement} className="card space-y-4 p-4">
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Mode</legend>
          {MODES.map((m) => (
            <label key={m.value} className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                value={m.value}
                defaultChecked={cfg.mode === m.value}
                disabled={!admin}
                className="mt-1"
              />
              <span>
                {m.label} <span className="text-zinc-500">— {m.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="grid gap-4 sm:grid-cols-3">
          {field("incident_below", "Open incident below")}
          {field("quarantine_below", "Quarantine below")}
          {field("revoke_below", "Revoke below")}
          {field("release_above", "Release at or above")}
          {field("min_quarantine_minutes", "Min quarantine (min)")}
          {field("decay_minutes", "Decay τ (min)", "0.5")}
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="auto_release" defaultChecked={cfg.auto_release} disabled={!admin} />
          Release automatic quarantines when trust recovers
        </label>
        {admin ? (
          <button className="btn btn-primary">Save</button>
        ) : (
          <p className="text-sm text-zinc-500">Only admins can change enforcement.</p>
        )}
      </ActionForm>
    </div>
  );
}
