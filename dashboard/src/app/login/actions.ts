"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ORCHESTRATOR_URL } from "@/lib/orchestrator";
import { ACCESS_COOKIE, REFRESH_COOKIE, cookieOptions } from "@/lib/session";

export type LoginState = { error?: string };

export async function login(_: LoginState, form: FormData): Promise<LoginState> {
  const res = await fetch(`${ORCHESTRATOR_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    cache: "no-store",
  }).catch(() => null);

  if (!res) return { error: "Orchestrator is unreachable" };
  if (res.status === 429) return { error: "Too many attempts, try again in a minute" };
  if (!res.ok) return { error: "Invalid username or password" };

  const { access, refresh } = (await res.json()) as { access: string; refresh: string };
  const jar = await cookies();
  jar.set(ACCESS_COOKIE, access, cookieOptions);
  jar.set(REFRESH_COOKIE, refresh, cookieOptions);

  const next = String(form.get("next") || "/");
  redirect(next.startsWith("/") && !next.startsWith("//") ? next : "/");
}

export async function logout() {
  const jar = await cookies();
  jar.delete(ACCESS_COOKIE);
  jar.delete(REFRESH_COOKIE);
  redirect("/login");
}
