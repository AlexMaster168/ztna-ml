import { NextResponse, type NextRequest } from "next/server";

import { LOCALE_COOKIE } from "@/i18n";
import { FLOW_COOKIE, OIDC, newFlow, redirectUri, ssoEnabled } from "@/lib/oidc";
import { cookieOptions } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Starts the authorization-code flow with PKCE; state, nonce and the verifier live in a
 * short-lived httpOnly cookie until the provider redirects back. */
export async function GET(req: NextRequest) {
  if (!ssoEnabled()) return NextResponse.redirect(new URL("/login", OIDC.appUrl));
  const flow = newFlow(req.nextUrl.searchParams.get("next") ?? "/");
  const auth = new URL(`${OIDC.publicUrl}/protocol/openid-connect/auth`);
  auth.search = new URLSearchParams({
    client_id: OIDC.clientId,
    response_type: "code",
    scope: "openid profile email",
    redirect_uri: redirectUri(),
    state: flow.state,
    nonce: flow.nonce,
    code_challenge: flow.challenge,
    code_challenge_method: "S256",
    ui_locales: req.cookies.get(LOCALE_COOKIE)?.value ?? "uk",
  }).toString();
  const res = NextResponse.redirect(auth);
  res.cookies.set(FLOW_COOKIE, JSON.stringify(flow), { ...cookieOptions, maxAge: 600 });
  return res;
}
