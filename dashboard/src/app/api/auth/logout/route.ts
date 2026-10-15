import { NextResponse, type NextRequest } from "next/server";

import { ID_TOKEN_COOKIE, OIDC, ssoEnabled } from "@/lib/oidc";
import { ACCESS_COOKIE, REFRESH_COOKIE } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Sign out. A GET navigation (not a form POST) so that an SSO session can continue to
 * the provider's end-session endpoint without widening the CSP form-action. Requests
 * started by other sites are ignored (Fetch Metadata), so logout cannot be forced. */
export async function GET(req: NextRequest) {
  const site = req.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") {
    return NextResponse.redirect(new URL("/", OIDC.appUrl));
  }
  const idToken = req.cookies.get(ID_TOKEN_COOKIE)?.value;
  let target = new URL("/login", OIDC.appUrl);
  if (idToken && ssoEnabled()) {
    // End the provider session too, otherwise "sign in with SSO" would log straight back in.
    target = new URL(`${OIDC.publicUrl}/protocol/openid-connect/logout`);
    target.search = new URLSearchParams({
      id_token_hint: idToken,
      post_logout_redirect_uri: `${OIDC.appUrl}/login`,
    }).toString();
  }
  const res = NextResponse.redirect(target);
  for (const name of [ACCESS_COOKIE, REFRESH_COOKIE, ID_TOKEN_COOKIE]) res.cookies.delete(name);
  return res;
}
