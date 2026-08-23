import { NextResponse } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, cookieBase } from "@/lib/session";

export async function POST(request: Request) {
  const refresh = request.headers.get("cookie")?.match(new RegExp(`${REFRESH_COOKIE}=([^;]+)`))?.[1];
  if (refresh) {
    await fetch(`${apiOrigin()}/api/v3/auth/revoke`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    }).catch(() => undefined);
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(ACCESS_COOKIE, "", { ...cookieBase, maxAge: 0 });
  response.cookies.set(REFRESH_COOKIE, "", { ...cookieBase, maxAge: 0 });
  return response;
}
