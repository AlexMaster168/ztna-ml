export const ACCESS_COOKIE = "ztna_access";
export const REFRESH_COOKIE = "ztna_refresh";

export const cookieOptions = {
  httpOnly: true,
  sameSite: "lax" as const,
  path: "/",
  // localhost counts as a secure context, so this stays on in dev too.
  secure: process.env.COOKIE_SECURE !== "0",
};

/** Seconds until the JWT expires (negative when expired). No signature check: the
 * orchestrator verifies tokens; this only decides when to refresh. */
export function secondsLeft(jwt: string): number {
  try {
    const payload = JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString());
    return payload.exp - Date.now() / 1000;
  } catch {
    return -1;
  }
}
