import { NextResponse, type NextRequest } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE, cookieOptions, secondsLeft } from "@/lib/session";

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";

/** Keeps the session alive: refreshes the access token shortly before it expires and
 * sends unauthenticated users to /login. */
export async function middleware(req: NextRequest) {
  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  if (access && secondsLeft(access) > 30) return NextResponse.next();

  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  if (refresh) {
    const res = await fetch(`${ORCHESTRATOR_URL}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    }).catch(() => null);
    if (res?.ok) {
      const tokens = (await res.json()) as { access: string; refresh?: string };
      // Make the new token visible to this request's server components...
      req.cookies.set(ACCESS_COOKIE, tokens.access);
      const out = NextResponse.next({ request: { headers: req.headers } });
      // ...and persist it in the browser.
      out.cookies.set(ACCESS_COOKIE, tokens.access, cookieOptions);
      if (tokens.refresh) out.cookies.set(REFRESH_COOKIE, tokens.refresh, cookieOptions);
      return out;
    }
  }

  const login = new URL("/login", req.url);
  login.searchParams.set("next", req.nextUrl.pathname);
  const out = NextResponse.redirect(login);
  out.cookies.delete(ACCESS_COOKIE);
  out.cookies.delete(REFRESH_COOKIE);
  return out;
}

export const config = {
  runtime: "nodejs",
  matcher: ["/((?!login|api/auth/|_next/|favicon.ico|robots.txt).*)"],
};
