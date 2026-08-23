import { NextResponse } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, cookieBase } from "@/lib/session";

export async function POST(request: Request) {
  const { idToken } = (await request.json()) as { idToken?: string };
  if (!idToken) {
    return NextResponse.json({ error: "missing_token", message: "idToken is required" }, { status: 400 });
  }
  const upstream = await fetch(`${apiOrigin()}/api/v3/auth/web/token`, {
    method: "POST",
    headers: { Authorization: `Bearer ${idToken}` },
  });
  const body = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    return NextResponse.json(body, { status: upstream.status });
  }
  const response = NextResponse.json({ ok: true, expires_in: body.expires_in });
  response.cookies.set(ACCESS_COOKIE, body.access_token, {
    ...cookieBase,
    maxAge: body.expires_in ?? 900,
  });
  response.cookies.set(REFRESH_COOKIE, body.refresh_token, {
    ...cookieBase,
    maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}
