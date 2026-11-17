// Expiry is read from the JWT's `exp` claim without verifying the signature -- the gateway
// still verifies every token it is handed; this only decides whether to refresh first. A token
// that does not decode is left for the gateway to judge.
export const EXPIRY_SKEW_S = 30;

export function accessExpired(token: string | undefined, nowS: number): boolean {
  if (!token) {
    return true;
  }
  const payload = token.split(".")[1];
  if (!payload) {
    return false;
  }
  try {
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const exp = (JSON.parse(json) as { exp?: unknown }).exp;
    return typeof exp === "number" && exp - EXPIRY_SKEW_S <= nowS;
  } catch {
    return false;
  }
}
