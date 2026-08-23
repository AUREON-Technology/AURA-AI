import { defineConfig } from "@playwright/test";

const apiUrl = process.env.AURA_API_URL || "http://127.0.0.1:8010";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000" },
  webServer: [
    {
      command: "node tests/e2e/mock-gateway.mjs",
      url: "http://127.0.0.1:8010",
      reuseExistingServer: !process.env.CI,
      env: { MOCK_GATEWAY_PORT: "8010" },
    },
    {
      command: "npx next dev --port 3000",
      url: "http://127.0.0.1:3000/login",
      reuseExistingServer: !process.env.CI,
      env: {
        AURA_API_URL: apiUrl,
        NEXT_PUBLIC_DEV_LOGIN: "1",
        NEXT_PUBLIC_GRAFANA_EMBED: "0",
      },
    },
  ],
});
