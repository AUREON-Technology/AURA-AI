import { NextResponse } from "next/server";

import { crossOriginRefusal } from "@/lib/same-origin";
import { REFRESH_COOKIE, apiOrigin, clearSessionCookies } from "@/lib/session";

export async function POST(request: Request) {
  // A cross-site page must not be able to sign the operator out (audit INT-009).
  const crossOrigin = crossOriginRefusal(request);
  if (crossOrigin) {
    return crossOrigin;
  }
  const refresh = request.headers.get("cookie")?.match(new RegExp(`${REFRESH_COOKIE}=([^;]+)`))?.[1];
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
