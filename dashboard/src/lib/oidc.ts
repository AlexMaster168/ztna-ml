import "server-only";

import { createHash, randomBytes } from "node:crypto";

/** OpenID Connect settings of the BFF. The browser talks to the identity provider's
 * public URL; the token exchange runs over the internal network. */
export const OIDC = {
  publicUrl: process.env.OIDC_PUBLIC_URL ?? "",
  internalUrl: process.env.OIDC_INTERNAL_URL ?? process.env.OIDC_PUBLIC_URL ?? "",
  clientId: process.env.OIDC_CLIENT_ID ?? "",
  clientSecret: process.env.OIDC_CLIENT_SECRET ?? "",
  appUrl: process.env.PUBLIC_URL ?? "http://localhost:3000",
};

export const ssoEnabled = () => Boolean(OIDC.publicUrl && OIDC.clientId);

export const FLOW_COOKIE = "ztna_oidc";
export const ID_TOKEN_COOKIE = "ztna_idt";
export const redirectUri = () => `${OIDC.appUrl}/api/auth/sso/callback`;

const b64url = (buf: Buffer) => buf.toString("base64url");

export function newFlow(next: string) {
  const verifier = b64url(randomBytes(32));
  return {
    state: b64url(randomBytes(16)),
    nonce: b64url(randomBytes(16)),
    verifier,
    challenge: b64url(createHash("sha256").update(verifier).digest()),
    next: next.startsWith("/") && !next.startsWith("//") ? next : "/",
  };
}

export type Flow = ReturnType<typeof newFlow>;
