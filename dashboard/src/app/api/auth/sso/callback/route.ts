import { NextResponse, type NextRequest } from "next/server";

import { LOCALE_COOKIE } from "@/i18n";
import { FLOW_COOKIE, ID_TOKEN_COOKIE, OIDC, redirectUri, type Flow } from "@/lib/oidc";
import { ORCHESTRATOR_URL } from "@/lib/orchestrator";
import { ACCESS_COOKIE, REFRESH_COOKIE, cookieOptions } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function fail(reason: string) {
  const url = new URL("/login", OIDC.appUrl);
  url.searchParams.set("sso_error", reason);
  const res = NextResponse.redirect(url);
  res.cookies.delete(FLOW_COOKIE);
  return res;
}

/** Provider redirect target: checks state, exchanges the code (with the PKCE verifier)
 * for an ID token and trades that for the orchestrator's own session tokens. The
 * orchestrator verifies the ID token itself; nothing here is trusted blindly. */
export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams;
  if (params.get("error")) return fail("denied");
  let flow: Flow;
  try {
    flow = JSON.parse(req.cookies.get(FLOW_COOKIE)?.value ?? "") as Flow;
  } catch {
    return fail("state");
  }
  if (!flow.state || params.get("state") !== flow.state) return fail("state");

  const tokenRes = await fetch(`${OIDC.internalUrl}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code: params.get("code") ?? "",
      redirect_uri: redirectUri(),
      client_id: OIDC.clientId,
      client_secret: OIDC.clientSecret,
      code_verifier: flow.verifier,
    }),
    cache: "no-store",
  }).catch(() => null);
  if (!tokenRes?.ok) return fail("exchange");
  const { id_token: idToken } = (await tokenRes.json()) as { id_token?: string };
  if (!idToken) return fail("exchange");

  const login = await fetch(`${ORCHESTRATOR_URL}/api/auth/oidc`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": req.cookies.get(LOCALE_COOKIE)?.value ?? "uk",
    },
    body: JSON.stringify({ id_token: idToken, nonce: flow.nonce }),
    cache: "no-store",
  }).catch(() => null);
  if (!login) return fail("unreachable");
  if (login.status === 403) return fail("forbidden");
  if (!login.ok) return fail("exchange");
  const { access, refresh } = (await login.json()) as { access: string; refresh: string };

  const res = NextResponse.redirect(new URL(flow.next, OIDC.appUrl));
  res.cookies.set(ACCESS_COOKIE, access, cookieOptions);
  res.cookies.set(REFRESH_COOKIE, refresh, cookieOptions);
  // Kept only as the logout hint for the provider's end-session endpoint.
  res.cookies.set(ID_TOKEN_COOKIE, idToken, cookieOptions);
  res.cookies.delete(FLOW_COOKIE);
  return res;
}
