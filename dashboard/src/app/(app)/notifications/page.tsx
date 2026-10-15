import { redirect } from "next/navigation";

import { ActionForm } from "@/components/action-form";
import { formatTime } from "@/components/format";
import { LiveRefresh } from "@/components/live";
import { StatusBadge } from "@/components/status-badge";
import { getT } from "@/i18n/server";
import { createChannel, deleteChannel, testChannel, toggleChannel } from "@/lib/actions";
import {
  api,
  can,
  type Delivery,
  type Me,
  type NotificationChannel,
  type Page,
} from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

const EVENTS = [
  "incident.opened",
  "peer.quarantined",
  "peer.revoked",
  "peer.released",
  "model.trained",
  "model.promoted",
];
const SEVERITIES = ["low", "medium", "high", "critical"];

export default async function NotificationsPage() {
  const [t, me] = await Promise.all([getT(), api<Me>("/api/me")]);
  if (!can(me, "analyst")) redirect("/");
  const admin = can(me, "admin");
  const [channels, deliveries] = await Promise.all([
    admin
      ? api<Page<NotificationChannel>>("/api/notifications/channels/?limit=100")
      : Promise.resolve({ count: 0, results: [] as NotificationChannel[] }),
    api<Page<Delivery>>("/api/notifications/deliveries?limit=50"),
  ]);

  return (
    <div className="space-y-6">
      <LiveRefresh types={["peer.status", "anomaly"]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("notify.title")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-500">{t("notify.intro")}</p>
      </div>

      {admin && (
        <section className="card overflow-x-auto">
          <h2 className="px-4 pt-4 font-medium">{t("notify.channels")}</h2>
          <table className="mt-2 w-full min-w-[760px] text-sm">
            <thead className="border-b border-zinc-200 text-left text-zinc-500 dark:border-zinc-800">
              <tr>
                <th className="px-4 py-2 font-medium">{t("notify.channel")}</th>
                <th className="px-4 py-2 font-medium">{t("notify.target")}</th>
                <th className="px-4 py-2 font-medium">{t("notify.filter")}</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {channels.results.map((c) => (
                <tr key={c.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                  <td className="px-4 py-2">
                    <div className="font-medium">{c.name}</div>
                    <div className="text-xs text-zinc-500">
                      {t.v("channelKind", c.kind)}
                      {!c.enabled && ` · ${t("notify.disabled")}`}
                    </div>
                  </td>
                  <td className="max-w-[260px] truncate px-4 py-2 font-mono text-xs">
                    {c.kind === "telegram" ? `chat ${c.telegram_chat_id}` : c.url}
                    {c.has_secret && c.kind === "webhook" && (
                      <div className="text-zinc-500">{t("notify.signed")}</div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {c.events.length ? c.events.map((e) => t.v("event", e)).join(", ") : t("notify.allEvents")}
                    <div className="text-zinc-500">
                      {t("notify.minSeverity", { s: t.v("severity", c.min_severity) })}
                    </div>
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex flex-wrap justify-end gap-2">
                      <ActionForm action={testChannel}>
                        <input type="hidden" name="id" value={c.id} />
                        <button className="btn btn-secondary py-1">{t("notify.test")}</button>
                      </ActionForm>
                      <ActionForm action={toggleChannel}>
                        <input type="hidden" name="id" value={c.id} />
                        <input type="hidden" name="enabled" value={String(!c.enabled)} />
                        <button className="btn btn-secondary py-1">
                          {c.enabled ? t("notify.disable") : t("notify.enable")}
                        </button>
                      </ActionForm>
                      <ActionForm action={deleteChannel}>
                        <input type="hidden" name="id" value={c.id} />
                        <button className="btn btn-danger py-1">{t("common.delete")}</button>
                      </ActionForm>
                    </div>
                  </td>
                </tr>
              ))}
              {channels.results.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-zinc-500">
                    {t("notify.noChannels")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <ActionForm
            action={createChannel}
            className="grid gap-3 border-t border-zinc-100 p-4 sm:grid-cols-3 dark:border-zinc-800"
          >
            <h3 className="text-sm font-medium sm:col-span-3">{t("notify.add")}</h3>
            <input name="name" required placeholder="soc-telegram" className="input" />
            <select name="kind" className="input" defaultValue="webhook">
              {["webhook", "slack", "telegram"].map((k) => (
                <option key={k} value={k}>
                  {t.v("channelKind", k)}
                </option>
              ))}
            </select>
            <select name="min_severity" className="input" defaultValue="medium">
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {t("notify.minSeverity", { s: t.v("severity", s) })}
                </option>
              ))}
            </select>
            <input name="url" placeholder={t("notify.urlPh")} className="input font-mono sm:col-span-3" />
            <input name="secret" type="password" placeholder={t("notify.secretPh")} className="input sm:col-span-2" />
            <input name="telegram_chat_id" placeholder={t("notify.chatPh")} className="input" />
            <div className="flex flex-wrap items-center gap-3 text-sm sm:col-span-3">
              <span className="text-zinc-500">{t("notify.events")}</span>
              {EVENTS.map((e) => (
                <label key={e} className="flex items-center gap-1">
                  <input type="checkbox" name="events" value={e} /> {t.v("event", e)}
                </label>
              ))}
              <button className="btn btn-primary ml-auto">{t("notify.addButton")}</button>
            </div>
          </ActionForm>
        </section>
      )}

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 font-medium">{t("notify.deliveries")}</h2>
        <table className="mt-2 w-full min-w-[760px] text-sm">
          <tbody>
            {deliveries.results.map((d) => (
              <tr key={d.id} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="whitespace-nowrap px-4 py-1.5 tabular-nums text-zinc-500">
                  {formatTime(d.created_at, t.locale)}
                </td>
                <td className="px-2 py-1.5">{d.channel_name}</td>
                <td className="px-2 py-1.5">
                  <StatusBadge status={d.status} label={t.v("delivery", d.status)} />
                </td>
                <td className="px-2 py-1.5">{d.text}</td>
                <td className="px-4 py-1.5 text-xs text-zinc-500">
                  {d.attempts > 1 && t("notify.attempts", { n: d.attempts })}
                  {d.last_error && ` · ${d.last_error}`}
                </td>
              </tr>
            ))}
            {deliveries.results.length === 0 && (
              <tr>
                <td className="px-4 py-6 text-center text-zinc-500">{t("common.nothing")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
