import { NextResponse } from "next/server";

import { crossOriginRefusal, nonJsonBodyRefusal } from "@/lib/same-origin";
import { CONSOLE_ROLE_MESSAGE, apiOrigin, setSessionCookies } from "@/lib/session";

async function revoke(refreshToken: string | undefined) {
  if (!refreshToken) {
    return;
  }
  await fetch(`${apiOrigin()}/api/v3/auth/revoke`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }).catch(() => undefined);
}

export async function POST(request: Request) {
  // Login CSRF: without this a sibling-subdomain page could sign the operator's browser into
  // another account (audit INT-009).
  const crossOrigin = crossOriginRefusal(request);
  if (crossOrigin) {
    return crossOrigin;
  }
  const raw = await request.text();
  const nonJson = nonJsonBodyRefusal(request, raw);
  if (nonJson) {
    return nonJson;
  }
  let idToken: string | undefined;
  try {
    idToken = (JSON.parse(raw || "{}") as { idToken?: string }).idToken;
  } catch {
    idToken = undefined;
  }
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

  const me = await fetch(`${apiOrigin()}/api/v3/admin/me`, {
    headers: { Authorization: `Bearer ${body.access_token}` },
    cache: "no-store",
  });
  const who = me.ok ? ((await me.json().catch(() => null)) as { is_admin?: boolean; role?: string } | null) : null;
  if (!who?.is_admin) {
    // The pair was minted; don't leave a live refresh token behind for a session we refuse.
    await revoke(body.refresh_token);
    return NextResponse.json(
      { error: "console_role_required", message: CONSOLE_ROLE_MESSAGE, role: who?.role ?? null },
      { status: 403 },
    );
  }

  const response = NextResponse.json({ ok: true, expires_in: body.expires_in });
  setSessionCookies(response.cookies, body);
  return response;
}
