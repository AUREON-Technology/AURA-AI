import { NextResponse } from "next/server";

// CSRF floor for every state-changing request the console serves (audit INT-009).
//
// The session cookies are SameSite=Strict, which stops a cross-*site* request but not a
// same-site one: a page on any sibling subdomain could otherwise POST to the proxy with the
// operator's cookies attached and fire a bodyless SuperAdmin action (forget, revoke-sessions,
// index rebuild, reset-circuit), or log the operator into an attacker's account.
//
// So a non-GET request must prove it came from the console's own pages:
//   - Sec-Fetch-Site, when the browser sends it, must be "same-origin";
//   - otherwise Origin must name this host. A request with neither is refused: every browser
//     the console supports sends at least one of them on a fetch() POST/PATCH/DELETE.
//   - A body, when there is one, must be declared application/json. A form or text/plain body
//     is what a cross-origin "simple request" can send without a preflight.
//
// The expected host is the one the browser addressed: X-Forwarded-Host, which nginx sets to
// $host (deploy/nginx/conf.d/proxy_headers.inc), else Host.

export const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function refuse(status: number, error: string, message: string): NextResponse {
  return NextResponse.json({ error, message, retryable: false }, { status });
}

function expectedHost(request: Request): string | null {
  const forwarded = request.headers.get("x-forwarded-host")?.split(",")[0]?.trim();
  return forwarded || request.headers.get("host") || null;
}

/** A refusal response when ``request`` is a state-changing request from another origin. */
export function crossOriginRefusal(request: Request): NextResponse | null {
  if (SAFE_METHODS.has(request.method.toUpperCase())) {
    return null;
  }
  const site = request.headers.get("sec-fetch-site");
  if (site !== null && site !== "same-origin") {
    return refuse(403, "cross_origin_refused", "Cross-site requests are refused.");
  }
  const origin = request.headers.get("origin");
  if (origin === null) {
    if (site === "same-origin") {
      return null;
    }
    return refuse(403, "cross_origin_refused", "A state-changing request must carry its Origin.");
  }
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return refuse(403, "cross_origin_refused", "Cross-site requests are refused.");
  }
  const host = expectedHost(request);
  if (!host || originHost !== host) {
    return refuse(403, "cross_origin_refused", "Cross-site requests are refused.");
  }
  return null;
}

/** A refusal when a request that has a body does not declare it as JSON. */
export function nonJsonBodyRefusal(request: Request, body: string | null): NextResponse | null {
  if (!body) {
    return null;
  }
  const type = (request.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") {
    return refuse(415, "unsupported_media_type", "Request bodies must be application/json.");
  }
  return null;
}
