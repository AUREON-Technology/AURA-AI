import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { type RotatedTokens, refreshAtGateway, singleFlightRefresh } from "@/lib/refresh";
import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, setSessionCookies } from "@/lib/session";

async function proxy(request: Request, path: string[]) {
  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  const target = `${apiOrigin()}/api/v3/${path.join("/")}${new URL(request.url).search}`;
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!["host", "connection", "cookie", "content-length"].includes(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  if (access) {
    headers.set("authorization", `Bearer ${access}`);
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.text(),
  };

  let upstream = await fetch(target, init);
  let rotated: RotatedTokens | null = null;
  if (upstream.status === 401 && refresh) {
    // Concurrent 401s carrying the same single-use refresh token share one refresh (F-35); each
    // of them then sets the new cookies below, whichever response the browser applies last.
    rotated = await singleFlightRefresh(refresh, refreshAtGateway);
    if (rotated) {
      headers.set("authorization", `Bearer ${rotated.access_token}`);
      upstream = await fetch(target, { ...init, headers });
    }
  }

  const payload = await upstream.arrayBuffer();
  const response = new NextResponse(payload, { status: upstream.status });
  upstream.headers.forEach((value, key) => {
    if (!["content-encoding", "transfer-encoding"].includes(key.toLowerCase())) {
      response.headers.set(key, value);
    }
  });
  if (rotated) {
    setSessionCookies(response.cookies, rotated);
  }
  return response;
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
export async function PATCH(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
export async function DELETE(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
