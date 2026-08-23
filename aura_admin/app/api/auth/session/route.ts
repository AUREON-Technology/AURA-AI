import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { ACCESS_COOKIE, apiOrigin } from "@/lib/session";

export async function GET() {
  const token = (await cookies()).get(ACCESS_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  const upstream = await fetch(`${apiOrigin()}/api/v3/admin/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!upstream.ok) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  const me = await upstream.json();
  if (!me.is_admin && me.role !== "Developer") {
    return NextResponse.json({ authenticated: false }, { status: 403 });
  }
  return NextResponse.json({ authenticated: true, role: me.role });
}
