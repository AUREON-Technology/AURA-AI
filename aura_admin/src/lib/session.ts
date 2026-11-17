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

// Shown when an account below Admin signs in. /api/v3/auth/web/token and /admin/me admit
// Developer, but every page's data route under /admin/* requires Admin
// (aura_backend/aura_core/api/admin.py, aura_backend/api/admin_platform.py), so a Developer
// session used to be ten pages of 403s (audit INT-042). The console refuses it up front instead,
// with the reason and the fix. The login page shows it for /login?reason=role.
export const CONSOLE_ROLE_MESSAGE =
  "The operator console needs the Admin role; your account has a lower role. Ask a SuperAdmin " +
  "to grant Admin on the Access page, then sign in again.";
