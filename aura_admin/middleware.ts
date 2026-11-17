import { type NextRequest, NextResponse } from "next/server";

import { accessExpired } from "@/lib/access-expiry";
import { refreshAtGateway, singleFlightRefresh } from "@/lib/refresh";
import { ACCESS_COOKIE, REFRESH_COOKIE, clearSessionCookies, setSessionCookies } from "@/lib/session";

// Session gate for console page renders. app/(console)/layout.tsx only reads the access cookie,
// and a server component cannot set cookies, so without this a render after the access token
// expired went to /login even though a valid refresh cookie was there. Here the refresh happens
// before the render: the rotated cookies go on the response for the browser, and on the
// forwarded request headers so the layout sees the new access token on this same request.
// Expiry is judged by src/lib/access-expiry.ts, shared with the /api/auth/session check.

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
