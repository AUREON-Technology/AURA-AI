import { expect, test, type Page } from "@playwright/test";

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";
const app = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";

// Regression tests for the console half of the 2026-11-17 integration audit. The mock gateway
// enforces roles and error shapes as the real handlers do (INT-035), so these fail against a
// console that only worked because the mock was permissive.

async function loginAs(page: Page, button: "Developer mock login" | "SuperAdmin mock login") {
  await page.goto("/login");
  await page.getByRole("button", { name: button }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
}

async function confirm(page: Page, text: string) {
  await page.getByLabel(`Type ${text}`).fill(text);
  await page.getByRole("button", { name: "Confirm" }).click();
}

test.describe("BFF proxy and auth routes refuse cross-site writes (INT-009)", () => {
  test("a write with a foreign Origin is refused before it reaches the gateway", async ({ page }) => {
    await loginAs(page, "Developer mock login");
    const response = await page.request.post("/api/v3/admin/providers/echo/reset-circuit", {
      headers: { origin: "https://evil.example" },
    });
    expect(response.status()).toBe(403);
    expect((await response.json()).error).toBe("cross_origin_refused");
  });

  test("a cross-site Sec-Fetch-Site is refused even with a matching Origin", async ({ page }) => {
    await loginAs(page, "Developer mock login");
    const response = await page.request.post("/api/v3/admin/providers/echo/reset-circuit", {
      headers: { origin: app, "sec-fetch-site": "same-site" },
    });
    expect(response.status()).toBe(403);
  });

  test("a write with neither Origin nor Sec-Fetch-Site is refused", async ({ page }) => {
    await loginAs(page, "Developer mock login");
    const response = await page.request.post("/api/v3/admin/providers/echo/reset-circuit");
    expect(response.status()).toBe(403);
  });

  test("a same-origin write with a text/plain body is refused", async ({ page }) => {
    await loginAs(page, "Developer mock login");
    const response = await page.request.post("/api/v3/admin/prompts/system.orchestrator/pin", {
      headers: { origin: app, "content-type": "text/plain" },
      data: JSON.stringify({ version: "1.0.0" }),
    });
    expect(response.status()).toBe(415);
  });

  test("a same-origin JSON write and a bodyless same-origin write still go through", async ({ page }) => {
    await loginAs(page, "Developer mock login");
    const statuses = await page.evaluate(async () => {
      const pin = await fetch("/api/v3/admin/prompts/system.orchestrator/pin", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ version: "1.0.0" }),
      });
      const reset = await fetch("/api/v3/admin/providers/echo/reset-circuit", { method: "POST" });
      return [pin.status, reset.status];
    });
    expect(statuses).toEqual([200, 200]);
  });

  test("only admin/* is proxied, and traversal segments are refused", async ({ page }) => {
    await loginAs(page, "Developer mock login");
    expect((await page.request.get("/api/v3/chat/sessions")).status()).toBe(404);
    expect((await page.request.get("/api/v3/auth/me")).status()).toBe(404);
    for (const path of ["/api/v3/admin/%2e%2e/chat", "/api/v3/admin/..%2fchat", "/api/v3/admin/.%2e/x"]) {
      const status = (await page.request.get(path)).status();
      expect([400, 404], path).toContain(status);
    }
  });

  test("login and logout refuse a foreign Origin", async ({ request }) => {
    const login = await request.post("/api/auth/login", {
      headers: { origin: "https://evil.example" },
      data: { idToken: "mock_token" },
    });
    expect(login.status()).toBe(403);
    expect(login.headers()["set-cookie"]).toBeUndefined();
    const logout = await request.post("/api/auth/logout", { headers: { origin: "https://evil.example" } });
    expect(logout.status()).toBe(403);
  });

  test("login refuses a body that is not JSON", async ({ request }) => {
    const login = await request.post("/api/auth/login", {
      headers: { origin: app, "content-type": "text/plain" },
      data: JSON.stringify({ idToken: "mock_token" }),
    });
    expect(login.status()).toBe(415);
  });
});

test("an incomplete GDPR forget is reported as incomplete, with what failed (INT-008)", async ({ page }) => {
  await loginAs(page, "SuperAdmin mock login");
  await page.getByRole("link", { name: "Access" }).click();
  await page.getByPlaceholder("email or auth subject").fill("partial@example.com");
  await page.getByRole("button", { name: "Forget" }).click();
  await confirm(page, "forget");
  const status = page.getByTestId("forget-status");
  await expect(status).toContainText("incomplete");
  await expect(status).toContainText("documents");
  await expect(page.getByTestId("forget-not-erased")).toContainText("feedback records");
});

test("an Admin cannot decide a blueprint deployment; the console does not offer it (INT-006)", async ({
  page,
}) => {
  await loginAs(page, "Developer mock login");
  await page.getByRole("link", { name: "Approvals" }).click();
  const row = page.getByRole("row").filter({ hasText: "Deploy blueprint v3" });
  await expect(row).toBeVisible();
  await expect(row.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(row.getByRole("button", { name: "Reject" })).toHaveCount(0);
  await expect(row).toContainText("SuperAdmin decides");

  // And the gateway (mock, mirroring admin.py) refuses it if called anyway.
  const status = await page.evaluate(async () =>
    (
      await fetch("/api/v3/admin/approvals/appr-bp/decide", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ approved: true }),
      })
    ).status,
  );
  expect(status).toBe(403);
});

test("a SuperAdmin is offered the decision on a blueprint deployment (INT-006)", async ({ page }) => {
  await loginAs(page, "SuperAdmin mock login");
  await page.getByRole("link", { name: "Approvals" }).click();
  const row = page.getByRole("row").filter({ hasText: "Deploy blueprint v3" });
  await expect(row.getByRole("button", { name: "Reject" })).toBeVisible();
});

test("a failed Enable/Disable shows the gateway's message (INT-032)", async ({ page }) => {
  await loginAs(page, "Developer mock login");
  await page.getByRole("link", { name: "Knowledge" }).click();
  const row = page.getByRole("row").filter({ hasText: "flaky" });
  await row.getByRole("button", { name: "Disable" }).click();
  await expect(page.getByText("the source registry is unavailable")).toBeVisible();
});

test("a namespaced local model is deleted with its '/' still encoded (INT-031)", async ({ page }) => {
  await loginAs(page, "SuperAdmin mock login");
  await page.getByRole("link", { name: "Models" }).click();
  const item = page.getByRole("listitem").filter({ hasText: "library/llama3:8b" });
  await item.getByRole("button", { name: "Delete" }).click();
  await confirm(page, "library/llama3:8b");
  await expect(page.getByRole("listitem").filter({ hasText: "library/llama3:8b" })).toHaveCount(0);
  const { paths } = await (await page.request.get(`${gateway}/__test/local-model-deletes`)).json();
  expect(paths).toContain("/api/v3/admin/local-models/library%2Fllama3%3A8b");
});

test("a Developer is told the console needs Admin, and gets no session (INT-042)", async ({ page }) => {
  await page.goto("/login");
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ idToken: "mock_token_dev" }),
    });
    return { status: response.status, body: await response.json() };
  });
  expect(result.status).toBe(403);
  expect(result.body.error).toBe("console_role_required");
  expect(result.body.message).toContain("Admin");
  const names = (await page.context().cookies()).map((c) => c.name);
  expect(names).not.toContain("aura_access");
  expect(names).not.toContain("aura_refresh");

  await page.goto("/login?reason=role");
  await expect(page.getByText("needs the Admin role")).toBeVisible();
});

test("the nginx auth_request session check refreshes an expired access token (INT-033)", async ({ page }) => {
  await loginAs(page, "Developer mock login");
  const access = (await page.context().cookies()).find((c) => c.name === "aura_access")?.value;
  const refresh = (await page.context().cookies()).find((c) => c.name === "aura_refresh")?.value;
  await page.request.post(`${gateway}/__test/expire-access`, { data: { access_token: access } });

  // As nginx's /_grafana_auth subrequest sends it: cookies, and the opt-in header that says
  // nginx will forward the rotated cookies to the browser.
  const checked = await fetch(`${app}/api/auth/session`, {
    headers: { cookie: `aura_refresh=${refresh}`, "x-aura-session-refresh": "1" },
  });
  expect(checked.status).toBe(200);
  const cookies = checked.headers.getSetCookie();
  expect(cookies.some((c) => c.startsWith("aura_access="))).toBe(true);
  expect(cookies.some((c) => c.startsWith("aura_refresh="))).toBe(true);

  // Without the opt-in the route never spends the single-use refresh token.
  const unchanged = await fetch(`${app}/api/auth/session`, { headers: { cookie: `aura_refresh=${refresh}` } });
  expect(unchanged.status).toBe(401);
});
