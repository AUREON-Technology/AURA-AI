import type { NextConfig } from "next";

import { API_CONTENT_SECURITY_POLICY, staticSecurityHeaders } from "./src/lib/security-headers";

// Empty on Railway, where the console has its own host (console.aureon-ai.tech) and serves from
// the root. The VPS compose files pass "/console", because there nginx shares one host between
// the console, the gateway and Grafana.
const basePath = process.env.NEXT_BASE_PATH || "";

const nextConfig: NextConfig = {
  // Required by aura_admin/Dockerfile (COPY .next/standalone). Without this,
  // `next build` does not emit standalone and the console image fails.
  output: "standalone",
  basePath,
  // Nginx is the slash policy. A Next 308 /console/ → /console plus an
  // edge 302 the other way is ERR_TOO_MANY_REDIRECTS after login.
  skipTrailingSlashRedirect: true,
  poweredByHeader: false,
  // Security headers on every response (audit F50): on Railway nothing sits in front of the
  // console to add them. Pages get their nonce CSP from middleware.ts; see
  // src/lib/security-headers.ts. Sources here are relative to basePath.
  async headers() {
    return [
      { source: "/:path*", headers: staticSecurityHeaders(basePath) },
      {
        source: "/api/:path*",
        headers: [{ key: "Content-Security-Policy", value: API_CONTENT_SECURITY_POLICY }],
      },
    ];
  },
  // The console used to live under /console on every host. Served from the root, old bookmarks
  // and links (/console/login, /console/providers) land on the same page without the prefix.
  // Redirects run before middleware.ts, so the session gate never sees a /console path. With a
  // base path set, /console *is* the console, so there is nothing to redirect.
  async redirects() {
    if (basePath) {
      return [];
    }
    return [
      { source: "/console", destination: "/", permanent: true },
      { source: "/console/:path*", destination: "/:path*", permanent: true },
    ];
  },
};

export default nextConfig;
