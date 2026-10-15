"use client";

import Link from "next/link";
import { useActionState } from "react";

import { useT } from "@/i18n/client";

import { createPeer, type ActionState } from "../actions";

export function NewPeerForm({ gateways, groups }: { gateways: string[]; groups: string[] }) {
  const t = useT();
  const [state, action, pending] = useActionState<ActionState, FormData>(createPeer, {});

  if (state.name) {
    const download = () => {
      const blob = new Blob([state.config ?? ""], { type: "text/plain" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${state.name}.conf`;
      a.click();
      URL.revokeObjectURL(a.href);
    };
    return (
      <div className="card space-y-4 p-4">
        <p className="text-sm">{t("newPeer.created", { name: state.name })}</p>
        {state.config ? (
          <>
            <p className="text-sm text-amber-700 dark:text-amber-400">{t("newPeer.onceWarning")}</p>
            <pre className="overflow-x-auto rounded-md bg-zinc-100 p-3 text-xs dark:bg-zinc-950">
              {state.config}
            </pre>
            <button onClick={download} className="btn btn-primary">
              {t("newPeer.download", { file: `${state.name}.conf` })}
            </button>
          </>
        ) : (
          <p className="text-sm text-zinc-500">{t("newPeer.ownKey")}</p>
        )}
        <div>
          <Link href="/nodes" className="text-sm underline">
            {t("newPeer.back")}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <form action={action} className="card space-y-4 p-4">
      <label className="block text-sm">
        {t("newPeer.name")}
        <input
          name="name"
          required
          pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{0,63}"
          className="input mt-1"
          placeholder="olena-laptop"
        />
      </label>
      <label className="block text-sm">
        {t("newPeer.gateway")}
        <select name="gateway" className="input mt-1">
          {gateways.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </label>
      {groups.length > 0 && (
        <fieldset className="text-sm">
          <legend>{t("newPeer.groups")}</legend>
          <div className="mt-1 flex flex-wrap gap-3">
            {groups.map((g) => (
              <label key={g} className="flex items-center gap-1.5">
                <input type="checkbox" name="groups" value={g} /> {g}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <label className="block text-sm">
        {t("newPeer.publicKey")} <span className="text-zinc-500">{t("newPeer.publicKeyHint")}</span>
        <input name="public_key" className="input mt-1 font-mono" />
      </label>
      {state.error && <p className="text-sm text-red-600">{state.error}</p>}
      <button disabled={pending} className="btn btn-primary">
        {pending ? t("newPeer.creating") : t("newPeer.create")}
      </button>
    </form>
  );
}
