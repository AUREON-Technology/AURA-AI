import { NextResponse } from "next/server";

import { crossOriginRefusal } from "@/lib/same-origin";
import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, clearSessionCookies } from "@/lib/session";

export async function POST(request: Request) {
  // A cross-site page must not be able to sign the operator out (audit INT-009).
  const crossOrigin = crossOriginRefusal(request);
  if (crossOrigin) {
    return crossOrigin;
  }
  const cookies = request.headers.get("cookie") ?? "";
  const access = cookies.match(new RegExp(`${ACCESS_COOKIE}=([^;]+)`))?.[1];
  const refresh = cookies.match(new RegExp(`${REFRESH_COOKIE}=([^;]+)`))?.[1];
  if (access) {
    // A Super Admin's step-up grant must not outlive the session: the access token stays valid
    // until it expires, and together with a grant it could still write (ADR-037). For anyone
    // else the gateway answers 403 and nothing happens.
    await fetch(`${apiOrigin()}/api/v3/admin/control/step-up/revoke`, {
      method: "POST",
      headers: { authorization: `Bearer ${access}` },
    }).catch(() => undefined);
  }
  if (refresh) {
    await fetch(`${apiOrigin()}/api/v3/auth/revoke`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    }).catch(() => undefined);
  }
  const response = NextResponse.json({ ok: true });
  clearSessionCookies(response.cookies);
  return response;
}
