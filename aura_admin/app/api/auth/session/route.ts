import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { accessExpired } from "@/lib/access-expiry";
import { type RotatedTokens, refreshAtGateway, singleFlightRefresh } from "@/lib/refresh";
import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, setSessionCookies } from "@/lib/session";

// The session check nginx's `auth_request` makes before serving /grafana/
// (deploy/nginx/templates/aura.conf.template, `location = /_grafana_auth`). It answers 200 for a
// signed-in Admin or SuperAdmin, 401 when there is no usable session, 403 below Admin
// (audit INT-042: every console page needs Admin).
//
// Refresh (audit INT-033): a Grafana panel left open past the 15-minute access token used to
// bounce to /login, because this check never refreshed. It now refreshes with the refresh
// cookie through the same single-flight state as the proxy and the middleware
// (src/lib/refresh.ts), and puts the rotated pair on its response as Set-Cookie.
//
// Refresh tokens are single use, and nginx drops an auth_request subrequest's response headers
// unless told otherwise. Refreshing here without nginx forwarding the new cookies would spend
// the browser's only refresh token and sign the operator out of the whole console about ten
// seconds later (REUSE_WINDOW_MS). So the refresh only happens when the caller opts in with
// `X-Aura-Session-Refresh: 1`, which the nginx location must set together with forwarding the
// cookies, e.g.:
//
//   location = /_grafana_auth {
//       ...
//       proxy_set_header X-Aura-Session-Refresh "1";
//   }
//   location /grafana/ {
//       auth_request /_grafana_auth;
//       auth_request_set $aura_access_cookie  $upstream_cookie_aura_access;
//       auth_request_set $aura_refresh_cookie $upstream_cookie_aura_refresh;
//       ... and re-emit both as Set-Cookie when non-empty, with the attributes in
//       src/lib/session.ts (HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age).
//
// Without that header the behaviour is what it was: no refresh, nothing spent.
const REFRESH_OPT_IN_HEADER = "x-aura-session-refresh";

function refreshWith(refresh: string): Promise<RotatedTokens | null> {
  return singleFlightRefresh(refresh, refreshAtGateway).catch(() => null);
}

async function whoAmI(access: string): Promise<Response> {
  return fetch(`${apiOrigin()}/api/v3/admin/me`, {
    headers: { Authorization: `Bearer ${access}` },
    cache: "no-store",
  });
}

export async function GET(request: Request) {
  const jar = await cookies();
  let access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  const refreshToken = refresh && request.headers.get(REFRESH_OPT_IN_HEADER) === "1" ? refresh : null;

  let rotated: RotatedTokens | null = null;
  if (refreshToken && accessExpired(access, Date.now() / 1000)) {
    rotated = await refreshWith(refreshToken);
    access = rotated?.access_token ?? access;
  }
  if (!access) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  let upstream = await whoAmI(access);
  if (upstream.status === 401 && refreshToken && !rotated) {
    rotated = await refreshWith(refreshToken);
    if (rotated) {
      upstream = await whoAmI(rotated.access_token);
    }
  }
  if (!upstream.ok) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  const me = (await upstream.json()) as { is_admin?: boolean; role?: string };
  if (!me.is_admin) {
    return NextResponse.json({ authenticated: false }, { status: 403 });
  }
  const response = NextResponse.json({ authenticated: true, role: me.role });
  if (rotated) {
    setSessionCookies(response.cookies, rotated);
  }
  return response;
}
