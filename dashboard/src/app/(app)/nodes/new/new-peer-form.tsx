"use client";

import Link from "next/link";
import { useActionState } from "react";

import { createPeer, type ActionState } from "../actions";

export function NewPeerForm() {
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
        <p className="text-sm">
          Peer <strong>{state.name}</strong> created.
        </p>
        {state.config ? (
          <>
            <p className="text-sm text-amber-700 dark:text-amber-400">
              This config contains the private key and is shown only once.
            </p>
            <pre className="overflow-x-auto rounded-md bg-zinc-100 p-3 text-xs dark:bg-zinc-950">
              {state.config}
            </pre>
            <button onClick={download} className="btn btn-primary">
              Download {state.name}.conf
            </button>
          </>
        ) : (
          <p className="text-sm text-zinc-500">
            Registered with your own public key; configure the client yourself.
          </p>
        )}
        <div>
          <Link href="/nodes" className="text-sm underline">
            Back to nodes
          </Link>
        </div>
      </div>
    );
  }

  return (
    <form action={action} className="card space-y-4 p-4">
      <label className="block text-sm">
        Name
        <input
          name="name"
          required
          pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{0,63}"
          className="input mt-1"
          placeholder="alice-laptop"
        />
      </label>
      <label className="block text-sm">
        Public key <span className="text-zinc-500">(optional, generated if empty)</span>
        <input name="public_key" className="input mt-1 font-mono" />
      </label>
      {state.error && <p className="text-sm text-red-600">{state.error}</p>}
      <button disabled={pending} className="btn btn-primary">
        {pending ? "Creating…" : "Create peer"}
      </button>
    </form>
  );
}
