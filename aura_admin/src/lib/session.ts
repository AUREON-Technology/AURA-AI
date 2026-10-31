export const ACCESS_COOKIE = "aura_access";
export const REFRESH_COOKIE = "aura_refresh";

export const cookieBase = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "strict" as const,
  path: "/",
};

export function apiOrigin(): string {
  return (process.env.AURA_API_URL || "http://localhost:8000").replace(/\/$/, "");
}

export const REFRESH_MAX_AGE_S = 60 * 60 * 24 * 30;

type CookieWriter = {
  set(name: string, value: string, options: typeof cookieBase & { maxAge: number }): unknown;
};

export function setSessionCookies(
  jar: CookieWriter,
  tokens: { access_token: string; refresh_token: string; expires_in?: number },
) {
  jar.set(ACCESS_COOKIE, tokens.access_token, { ...cookieBase, maxAge: tokens.expires_in ?? 900 });
  jar.set(REFRESH_COOKIE, tokens.refresh_token, { ...cookieBase, maxAge: REFRESH_MAX_AGE_S });
}

export function clearSessionCookies(jar: CookieWriter) {
  jar.set(ACCESS_COOKIE, "", { ...cookieBase, maxAge: 0 });
  jar.set(REFRESH_COOKIE, "", { ...cookieBase, maxAge: 0 });
}
