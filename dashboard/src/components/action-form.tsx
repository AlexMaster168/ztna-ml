"use client";

import { useActionState } from "react";

export type FormState = { error?: string; ok?: boolean };
type Action = (state: FormState, form: FormData) => Promise<FormState>;

/** A form bound to a server action that shows the action's error inline. */
export function ActionForm({
  action,
  children,
  className = "flex flex-wrap items-center gap-2",
}: {
  action: Action;
  children: React.ReactNode;
  className?: string;
}) {
  const [state, formAction, pending] = useActionState<FormState, FormData>(action, {});
  return (
    <form action={formAction} className={className}>
      <fieldset disabled={pending} className="contents">
        {children}
      </fieldset>
      {state.error && (
        <span role="alert" className="text-xs text-red-600 dark:text-red-400">
          {state.error}
        </span>
      )}
    </form>
  );
}
