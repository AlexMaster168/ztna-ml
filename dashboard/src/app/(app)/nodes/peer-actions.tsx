"use client";

import { useActionState } from "react";

import { useT } from "@/i18n/client";
import type { PeerStatus } from "@/lib/orchestrator";

import { changeStatus, type ActionState } from "./actions";

type Op = "quarantine" | "release" | "revoke";

// One form per operation with a hidden field: the submitter button's name/value is not
// reliably part of the FormData a server action receives.
function OpButton({
  id,
  op,
  label,
  danger,
}: {
  id: number;
  op: Op;
  label: string;
  danger?: boolean;
}) {
  const [state, action, pending] = useActionState<ActionState, FormData>(changeStatus, {});
  return (
    <form action={action} className="flex items-center gap-1">
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="op" value={op} />
      <button disabled={pending} className={`btn ${danger ? "btn-danger" : "btn-secondary"}`}>
        {label}
      </button>
      {state.error && <span className="text-xs text-red-600">{state.error}</span>}
    </form>
  );
}

export function PeerActions({
  id,
  status,
  canAnalyst,
  canAdmin,
}: {
  id: number;
  status: PeerStatus;
  canAnalyst: boolean;
  canAdmin: boolean;
}) {
  const t = useT();
  if (status === "revoked") return null;
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {canAnalyst && status === "active" && (
        <OpButton id={id} op="quarantine" label={t("peer.quarantine")} />
      )}
      {canAnalyst && status === "quarantined" && (
        <OpButton id={id} op="release" label={t("peer.release")} />
      )}
      {canAdmin && <OpButton id={id} op="revoke" label={t("peer.revoke")} danger />}
    </div>
  );
}
