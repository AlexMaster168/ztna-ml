"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { getT } from "@/i18n/server";
import { ORCHESTRATOR_URL } from "@/lib/orchestrator";
import { ACCESS_COOKIE, REFRESH_COOKIE, cookieOptions } from "@/lib/session";

export type LoginState = { error?: string };

export async function login(_: LoginState, form: FormData): Promise<LoginState> {
  const t = await getT();
  const res = await fetch(`${ORCHESTRATOR_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    cache: "no-store",
  }).catch(() => null);

  if (!res) return { error: t("login.unreachable") };
  if (res.status === 429) return { error: t("login.throttled") };
  if (!res.ok) return { error: t("login.invalid") };

  const { access, refresh } = (await res.json()) as { access: string; refresh: string };
  const jar = await cookies();
  jar.set(ACCESS_COOKIE, access, cookieOptions);
  jar.set(REFRESH_COOKIE, refresh, cookieOptions);

  const next = String(form.get("next") || "/");
  redirect(next.startsWith("/") && !next.startsWith("//") ? next : "/");
}
