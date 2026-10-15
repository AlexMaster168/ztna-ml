"use client";

import { useActionState } from "react";

import { useT } from "@/i18n/client";

import { login, type LoginState } from "./actions";

export function LoginForm({ next }: { next: string }) {
  const t = useT();
  const [state, action, pending] = useActionState<LoginState, FormData>(login, {});
  return (
    <form action={action} className="mt-6 space-y-4">
      <input type="hidden" name="next" value={next} />
      <label className="block text-sm">
        {t("login.username")}
        <input name="username" required autoComplete="username" className="input mt-1" />
      </label>
      <label className="block text-sm">
        {t("login.password")}
        <input
          name="password"
          type="password"
          required
          autoComplete="current-password"
          className="input mt-1"
        />
      </label>
      {state.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {state.error}
        </p>
      )}
      <button type="submit" disabled={pending} className="btn btn-secondary w-full">
        {pending ? t("login.signingIn") : t("login.signIn")}
      </button>
    </form>
  );
}
