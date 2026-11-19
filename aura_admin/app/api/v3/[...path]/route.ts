import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { type RotatedTokens, refreshAtGateway, singleFlightRefresh } from "@/lib/refresh";
import { crossOriginRefusal, nonJsonBodyRefusal } from "@/lib/same-origin";
import { ACCESS_COOKIE, REFRESH_COOKIE, apiOrigin, setSessionCookies } from "@/lib/session";

// The only gateway area the console's pages call through this proxy (every `api()` call in
// app/ and src/ is under /admin). Anything else -- /chat, /auth/*, /sync -- is not the
// console's to forward with an operator's token (audit INT-009). Login, logout and the session
// check talk to the gateway from their own routes, not through here.
const ALLOWED_PREFIXES = new Set(["admin"]);

// Next hands the catch-all its segments percent-decoded, so "%2e%2e" arrives as "..". Such a
// segment would let fetch() normalise the target out of /api/v3/admin, so it is refused rather
// than encoded.
function badSegment(segment: string): boolean {
  return segment === "" || segment === "." || segment === ".." || segment.includes("\\");
}

function refuse(status: number, error: string, message: string) {
  return NextResponse.json({ error, message, retryable: false }, { status });
}

async function proxy(request: Request, path: string[]) {
  if (!path.length || !ALLOWED_PREFIXES.has(path[0])) {
    return refuse(404, "not_found", "Not proxied by the console.");
  }
  if (path.some(badSegment)) {
    return refuse(400, "invalid_path", "Path segments may not be empty, '.', '..' or contain '\\'.");
  }
  const crossOrigin = crossOriginRefusal(request);
  if (crossOrigin) {
    return crossOrigin;
  }
  const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.text();
  const nonJson = nonJsonBodyRefusal(request, body ?? null);
  if (nonJson) {
    return nonJson;
  }

  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  // Each segment is re-encoded, so a '/' that arrived as %2F in one segment (a namespaced
  // Ollama tag, audit INT-031) stays one segment at the gateway instead of becoming two.
  const target = `${apiOrigin()}/api/v3/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`;
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
    body,
  };

  let upstream = await fetch(target, init);
  let rotated: RotatedTokens | null = null;
  if (upstream.status === 401 && refresh) {
    // Concurrent 401s carrying the same single-use refresh token share one refresh (F-35); each
    // of them then sets the new cookies below, whichever response the browser applies last.
    const outcome = await singleFlightRefresh(refresh, refreshAtGateway);
    if (outcome.ok) {
      rotated = outcome.tokens;
      headers.set("authorization", `Bearer ${rotated.access_token}`);
      upstream = await fetch(target, { ...init, headers });
    } else if (outcome.reason === "unavailable") {
      // The gateway could not answer the refresh (INT-104). Passing the 401 on would make
      // api.ts send the operator to /login over an outage; the refresh cookie is still good.
      return NextResponse.json(
        {
          error: "dependency_unavailable",
          message: "The gateway could not renew your session just now. You are still signed in; try again shortly.",
          retryable: true,
        },
        { status: 503, headers: { "retry-after": "5" } },
      );
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
