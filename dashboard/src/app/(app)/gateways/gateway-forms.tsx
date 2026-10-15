"use client";

import { useActionState } from "react";

import { useT } from "@/i18n/client";
import { createGateway, rotateGatewayToken, type TokenState } from "@/lib/actions";

function TokenReveal({ state }: { state: TokenState }) {
  const t = useT();
  if (!state.token) return null;
  return (
    <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950">
      <p>{t("gateways.tokenOnce", { name: state.name ?? "" })}</p>
      <pre className="overflow-x-auto rounded bg-white p-2 font-mono text-xs dark:bg-zinc-950">
        {state.token}
      </pre>
      <p className="text-xs text-zinc-600 dark:text-zinc-400">{t("gateways.tokenHint")}</p>
    </div>
  );
}

export function RegisterGatewayForm() {
  const t = useT();
  const [state, action, pending] = useActionState<TokenState, FormData>(createGateway, {});
  return (
    <form action={action} className="space-y-3 border-t border-zinc-100 p-4 dark:border-zinc-800">
      <h3 className="text-sm font-medium">{t("gateways.register")}</h3>
      <div className="grid gap-3 sm:grid-cols-3">
        <input name="name" required pattern="[a-z0-9-]+" placeholder="gw-kyiv" className="input" />
        <input name="endpoint" required placeholder="vpn.example.org:51820" className="input font-mono" />
        <input name="wg_subnet" required placeholder="10.15.0.0/24" className="input font-mono" />
      </div>
      {state.error && <p className="text-sm text-red-600">{state.error}</p>}
      <TokenReveal state={state} />
      <button disabled={pending} className="btn btn-primary">
        {t("gateways.registerButton")}
      </button>
    </form>
  );
}

export function RotateTokenForm({ id, name }: { id: number; name: string }) {
  const t = useT();
  const [state, action, pending] = useActionState<TokenState, FormData>(rotateGatewayToken, {});
  return (
    <form action={action} className="space-y-2">
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="name" value={name} />
      <button disabled={pending} className="btn btn-secondary py-1">
        {t("gateways.rotate")}
      </button>
      {state.error && <p className="text-xs text-red-600">{state.error}</p>}
      <TokenReveal state={state} />
    </form>
  );
}
