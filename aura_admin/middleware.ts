import { type NextRequest, NextResponse } from "next/server";

import { refreshAtGateway, singleFlightRefresh } from "@/lib/refresh";
import { ACCESS_COOKIE, REFRESH_COOKIE, clearSessionCookies, setSessionCookies } from "@/lib/session";

// Session gate for console page renders. app/(console)/layout.tsx only reads the access cookie,
// and a server component cannot set cookies, so without this a render after the access token
// expired went to /login even though a valid refresh cookie was there. Here the refresh happens
// before the render: the rotated cookies go on the response for the browser, and on the
// forwarded request headers so the layout sees the new access token on this same request.
//
// Expiry is read from the JWT's `exp` claim without verifying the signature -- the gateway
// still verifies every token it is handed; this only decides whether to refresh first. A token
// that does not decode is left for the gateway to judge.
const EXPIRY_SKEW_S = 30;

function accessExpired(token: string | undefined, nowS: number): boolean {
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

export async function middleware(request: NextRequest) {
  const access = request.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  if (!refresh || !accessExpired(access, Date.now() / 1000)) {
    // Nothing to refresh with, or nothing to refresh: the layout decides.
    return NextResponse.next();
  }

  // Same single-flight state as the /api/v3 proxy (src/lib/refresh.ts), so a navigation, its
  // prefetches and any proxied fetch carrying this refresh token spend it once between them.
  const rotated = await singleFlightRefresh(refresh, refreshAtGateway).catch(() => null);
  if (!rotated) {
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = "";
    const response = NextResponse.redirect(login);
    clearSessionCookies(response.cookies);
    return response;
  }

  request.cookies.set(ACCESS_COOKIE, rotated.access_token);
  request.cookies.set(REFRESH_COOKIE, rotated.refresh_token);
  const headers = new Headers(request.headers);
  headers.set("cookie", request.cookies.toString());
  const response = NextResponse.next({ request: { headers } });
  setSessionCookies(response.cookies, rotated);
  return response;
}

export const config = {
  // Node.js, not Edge: the single-flight state in src/lib/refresh.ts lives on globalThis and
  // must be the same object the proxy route uses. Stable in Next 15.5.
  runtime: "nodejs",
  // Console pages only: not /login, not /api/* (the proxy refreshes on its own 401s), not
  // Next's assets or any file with an extension.
  matcher: ["/((?!api/|api$|login|_next/static|_next/image|favicon\\.ico|.*\\..*).*)"],
};
