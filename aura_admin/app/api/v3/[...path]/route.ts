import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, cookieBase } from "@/lib/session";

async function refreshAccess(refreshToken: string) {
  const upstream = await fetch(`${apiOrigin()}/api/v3/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!upstream.ok) {
    return null;
  }
  return (await upstream.json()) as {
    access_token: string;
    refresh_token: string;
    expires_in: number;
  };
}

async function proxy(request: Request, path: string[]) {
  const jar = await cookies();
  let access = jar.get(ACCESS_COOKIE)?.value;
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
  let rotated: Awaited<ReturnType<typeof refreshAccess>> | null = null;
  if (upstream.status === 401 && refresh) {
    rotated = await refreshAccess(refresh);
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
    response.cookies.set(ACCESS_COOKIE, rotated.access_token, {
      ...cookieBase,
      maxAge: rotated.expires_in ?? 900,
    });
    response.cookies.set(REFRESH_COOKIE, rotated.refresh_token, {
      ...cookieBase,
      maxAge: 60 * 60 * 24 * 30,
    });
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
