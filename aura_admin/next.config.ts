import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required by aura_admin/Dockerfile (COPY .next/standalone). Without this,
  // `next build` does not emit standalone and the console image fails.
  output: "standalone",
  basePath: process.env.NEXT_BASE_PATH || "",
  // Nginx is the slash policy. A Next 308 /console/ → /console plus an
  // edge 302 the other way is ERR_TOO_MANY_REDIRECTS after login.
  skipTrailingSlashRedirect: true,
  poweredByHeader: false,
};

export default nextConfig;
