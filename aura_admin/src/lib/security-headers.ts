// Response security headers (audit F50). On the VPS, nginx adds these in front of the console
// (deploy/nginx/templates/aura.conf.template, `location /console`); on Railway the console is its
// own host with no proxy in front, so the app has to send them itself. The values match nginx's
// so the two targets behave the same; where both send a CSP, a browser enforces both, and every
// source this policy allows is one nginx's allows too.
//
// next.config.ts sends the static headers on every response and the locked-down API policy on
// /api/*. Pages get their CSP from middleware.ts instead, because it carries a fresh nonce per
// request: Next's App Router streams inline <script> tags (the RSC payload, the bootstrap), so
// `script-src 'self'` alone would block hydration, and 'unsafe-inline' would admit any injected
// script. Next reads the nonce back from the request's Content-Security-Policy header and puts it
// on every script it renders; 'strict-dynamic' then extends trust to the chunks and the Google
// sign-in script those scripts load.

// Served from the root of its own host (Railway), nothing frames the console. Under a base path
// (the VPS), nginx serves Grafana on the same host and sends an expired Grafana session to the
// console's login page inside the Grafana iframe (`location @grafana_unauth`), so the console
// may be framed by its own origin there, as nginx's own CSP for /console already allows.
export type FrameAncestors = "'none'" | "'self'";

export function frameAncestorsFor(basePath: string): FrameAncestors {
  return basePath ? "'self'" : "'none'";
}

export function staticSecurityHeaders(basePath: string): Array<{ key: string; value: string }> {
  return [
    { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "geolocation=(), microphone=(), camera=()" },
    { key: "X-Frame-Options", value: basePath ? "SAMEORIGIN" : "DENY" },
  ];
}

// JSON and redirects only: nothing under /api renders a document.
export const API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'";

const GOOGLE_SIGN_IN = "https://accounts.google.com";

export function pageContentSecurityPolicy({
  nonce,
  dev,
  frameAncestors,
}: {
  nonce: string;
  /** `next dev` evaluates code for its overlay and hot reload, and talks to it over a WebSocket. */
  dev: boolean;
  frameAncestors: FrameAncestors;
}): string {
  const directives: Array<[string, string[]]> = [
    ["default-src", ["'self'"]],
    // The Google host is only for browsers without 'strict-dynamic', which ignore the nonce's
    // trust propagation; browsers that support it ignore host sources here.
    ["script-src", ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", GOOGLE_SIGN_IN, ...(dev ? ["'unsafe-eval'"] : [])]],
    // React style attributes (style={{...}}) cannot carry a nonce, so inline styles stay allowed,
    // as they are under nginx's policy. Styles cannot run script.
    ["style-src", ["'self'", "'unsafe-inline'", `${GOOGLE_SIGN_IN}/gsi/style`]],
    ["img-src", ["'self'", "data:"]],
    ["font-src", ["'self'"]],
    // The browser only ever calls the console's own routes (the gateway is reached server-side
    // through AURA_API_URL), plus Google's sign-in endpoints.
    ["connect-src", ["'self'", GOOGLE_SIGN_IN, "https://oauth2.googleapis.com", ...(dev ? ["ws:"] : [])]],
    // 'self' for the Grafana embeds (/grafana/ on the VPS host), Google for its sign-in button.
    ["frame-src", ["'self'", GOOGLE_SIGN_IN]],
    ["frame-ancestors", [frameAncestors]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
  ];
  return directives.map(([name, values]) => `${name} ${values.join(" ")}`).join("; ");
}
