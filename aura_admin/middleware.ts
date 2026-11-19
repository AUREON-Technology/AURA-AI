import { type NextRequest, NextResponse } from "next/server";

import { accessExpired } from "@/lib/access-expiry";
import { type RotatedTokens, refreshAtGateway, singleFlightRefresh } from "@/lib/refresh";
import { frameAncestorsFor, pageContentSecurityPolicy } from "@/lib/security-headers";
import { ACCESS_COOKIE, REFRESH_COOKIE, clearSessionCookies, setSessionCookies } from "@/lib/session";

// Two jobs for every page render:
//
// 1. The page's Content-Security-Policy, with a fresh nonce (audit F50, src/lib/security-headers.ts).
//    Next reads the nonce from the forwarded request's CSP header and stamps it on its scripts.
//
// 2. The session gate for console pages. app/(console)/layout.tsx only reads the access cookie,
//    and a server component cannot set cookies, so without this a render after the access token
//    expired went to /login even though a valid refresh cookie was there. Here the refresh
//    happens before the render: the rotated cookies go on the response for the browser, and on
//    the forwarded request headers so the layout sees the new access token on this same request.
//    Expiry is judged by src/lib/access-expiry.ts, shared with the /api/auth/session check.
//
//    Only the gateway refusing the refresh token ends the session. An outage (INT-104: a 5xx, an
//    unreachable gateway) answers 503 and keeps both cookies, so every operator is not signed out
//    by a gateway blip; the next render after the gateway is back refreshes as usual.

const OUTAGE_RETRY_S = 10;

const OUTAGE_PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="${OUTAGE_RETRY_S}">
<title>AURA Console: gateway unavailable</title>
</head>
<body>
<h1>The gateway is unavailable</h1>
<p>The console could not renew your session because the AURA gateway did not answer. You are
still signed in. This page tries again in ${OUTAGE_RETRY_S} seconds.</p>
</body>
</html>
`;

type Gate =
  | { kind: "pass" }
  | { kind: "rotated"; tokens: RotatedTokens }
  | { kind: "signed-out" }
  | { kind: "unavailable" };

function isLoginPage(pathname: string): boolean {
  return pathname === "/login" || pathname.startsWith("/login/");
}

async function sessionGate(request: NextRequest): Promise<Gate> {
  if (isLoginPage(request.nextUrl.pathname)) {
    return { kind: "pass" };
  }
  const access = request.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  if (!refresh || !accessExpired(access, Date.now() / 1000)) {
    // Nothing to refresh with, or nothing to refresh: the layout decides.
    return { kind: "pass" };
  }
  // Same single-flight state as the /api/v3 proxy (src/lib/refresh.ts), so a navigation, its
  // prefetches and any proxied fetch carrying this refresh token spend it once between them.
  const outcome = await singleFlightRefresh(refresh, refreshAtGateway);
  if (outcome.ok) {
    return { kind: "rotated", tokens: outcome.tokens };
  }
  return outcome.reason === "rejected" ? { kind: "signed-out" } : { kind: "unavailable" };
}

export async function middleware(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = pageContentSecurityPolicy({
    nonce,
    dev: process.env.NODE_ENV === "development",
    frameAncestors: frameAncestorsFor(request.nextUrl.basePath),
  });

  const gate = await sessionGate(request);
  if (gate.kind === "signed-out") {
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = "";
    const response = NextResponse.redirect(login);
    clearSessionCookies(response.cookies);
    return response;
  }
  if (gate.kind === "unavailable") {
    return new NextResponse(OUTAGE_PAGE, {
      status: 503,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "retry-after": String(OUTAGE_RETRY_S),
        "content-security-policy": csp,
      },
    });
  }

  if (gate.kind === "rotated") {
    request.cookies.set(ACCESS_COOKIE, gate.tokens.access_token);
    request.cookies.set(REFRESH_COOKIE, gate.tokens.refresh_token);
  }
  const headers = new Headers(request.headers);
  if (gate.kind === "rotated") {
    headers.set("cookie", request.cookies.toString());
  }
  headers.set("x-nonce", nonce);
  headers.set("content-security-policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("content-security-policy", csp);
  if (gate.kind === "rotated") {
    setSessionCookies(response.cookies, gate.tokens);
  }
  return response;
}

export const config = {
  // Node.js, not Edge: the single-flight state in src/lib/refresh.ts lives on globalThis and
  // must be the same object the proxy route uses. Stable in Next 15.5.
  runtime: "nodejs",
  // Every page, /login included (it needs its CSP nonce; the session gate skips it). Not /api/*
  // (the proxy refreshes on its own 401s, and next.config.ts gives API responses their own CSP),
  // not Next's assets or any file with an extension.
  matcher: ["/((?!api/|api$|_next/static|_next/image|favicon\\.ico|.*\\..*).*)"],
};
