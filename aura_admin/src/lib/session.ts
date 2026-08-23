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
