import { defineConfig } from "@playwright/test";

// Ports are overridable so two checkouts can run the suite on one host at the same time.
const gatewayPort = process.env.MOCK_GATEWAY_PORT || "8010";
const appPort = process.env.PLAYWRIGHT_APP_PORT || "3000";
const apiUrl = process.env.AURA_API_URL || `http://127.0.0.1:${gatewayPort}`;
const baseURL = process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${appPort}`;
// The specs read these to reach the mock gateway's test hooks and to scope cookies.
process.env.AURA_API_URL = apiUrl;
process.env.PLAYWRIGHT_BASE_URL = baseURL;

export default defineConfig({
  testDir: "tests",
  timeout: 60_000,
  // `next dev` compiles each route on its first request: a cold sign-in compiles the login
  // route, the middleware and the console layout in turn, which on a 4-core runner exceeds
  // Playwright's default 5 s for the first heading to appear.
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    // For a host whose preinstalled Chromium is a different revision from this @playwright/test.
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined },
  },
  webServer: [
    {
      command: "node tests/e2e/mock-gateway.mjs",
      url: `http://127.0.0.1:${gatewayPort}`,
      reuseExistingServer: !process.env.CI,
      env: { MOCK_GATEWAY_PORT: gatewayPort },
    },
    {
      command: `npx next dev --port ${appPort}`,
      url: `http://127.0.0.1:${appPort}/login`,
      reuseExistingServer: !process.env.CI,
      env: {
        AURA_API_URL: apiUrl,
        NEXT_PUBLIC_DEV_LOGIN: "1",
        NEXT_PUBLIC_GRAFANA_EMBED: "0",
      },
    },
  ],
});
