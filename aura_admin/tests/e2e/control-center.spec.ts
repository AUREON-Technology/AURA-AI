import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// The Super-Admin control center, stage 1 (ADR-037, docs/plans/console-control-center-plan.md).
// The mock gateway enforces what aura_core/api/admin_control.py enforces: SuperAdmin only, a
// step-up grant on every write (bought with a one-time challenge), the typed confirmation, and
// tokens that are never returned.

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";

async function seen(request: APIRequestContext) {
  return (await (await request.get(`${gateway}/__test/control`)).json()) as {
    step_ups: number;
    tokens_received: string[];
    live_grants: number;
  };
}

test.beforeEach(async ({ request }) => {
  await request.post(`${gateway}/__test/reset-control`);
});

async function signIn(page: Page, button: "Developer mock login" | "SuperAdmin mock login") {
  await page.goto("/login");
  await page.getByRole("button", { name: button }).click();
  // The login page has its own h1, so wait for the redirect away from it, not for a heading.
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

async function openSettings(page: Page) {
  await signIn(page, "SuperAdmin mock login");
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
}

test("an Admin has no Settings link, and opening the URL shows a refusal and loads nothing", async ({ page }) => {
  await signIn(page, "Developer mock login");
  await expect(page.getByRole("link", { name: "Access" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings" })).toHaveCount(0);

  const controlCalls: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/admin/control/")) controlCalls.push(req.url());
  });
  await page.goto("/settings");
  await expect(page.getByTestId("settings-super-only")).toHaveText("Only the Super Admin can open Settings.");
  await expect(page.getByTestId("platform-railway")).toHaveCount(0);
  expect(controlCalls).toEqual([]);
});

test("the Super Admin connects a platform: re-sign-in, typed confirm, and the token never comes back", async ({
  page,
  request,
}) => {
  await openSettings(page);
  await expect(page.getByTestId("settings-release")).toContainText("mock123");
  const hf = page.getByTestId("platform-hf_space");
  await expect(hf).toContainText("Not connected");

  await hf.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("hf_secret_value_123");
  await page.getByLabel("Space (owner/name)").fill("org/aura-inference");
  await page.getByLabel("Type hf_space").fill("hf_space");
  await page.getByRole("button", { name: "Confirm" }).click();

  await expect(hf).toContainText("Verified");
  await expect(hf).toContainText("org/aura-inference");
  await expect(page.getByText("hf_secret_value_123")).toHaveCount(0);
  expect(await page.content()).not.toContain("hf_secret_value_123");
  const after = await seen(request);
  expect(after.step_ups).toBe(1);
  expect(after.tokens_received).toEqual(["hf_secret_value_123"]);

  // A second write inside the grant's five minutes needs no new sign-in.
  await page.getByTestId("platform-railway").getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("railway_secret");
  await page.getByLabel("Type railway").fill("railway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("platform-railway")).toContainText("Saved, not verified yet");
  expect((await seen(request)).step_ups).toBe(1);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row")).toHaveCount(2);
  await expect(page.getByTestId("change-row").first()).toContainText("Connected Railway");
  expect(await page.content()).not.toContain("railway_secret");
});

test("signing out drops the step-up grant on the gateway too", async ({ page, request }) => {
  await openSettings(page);
  await page.getByTestId("platform-railway").getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("railway_secret");
  await page.getByLabel("Type railway").fill("railway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("platform-railway")).toContainText("Saved, not verified yet");
  expect((await seen(request)).live_grants).toBe(1);

  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL((url) => url.pathname.startsWith("/login"));
  expect((await seen(request)).live_grants).toBe(0);
});

test("a wrong confirmation word is refused before anything is sent", async ({ page, request }) => {
  await openSettings(page);
  await page.getByTestId("platform-qdrant").getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("qdrant-key");
  await page.getByLabel("Type qdrant").fill("yes");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("qdrant").last()).toBeVisible();
  expect((await seen(request)).tokens_received).toEqual([]);
});

test("a token the platform refuses shows why and stays disconnected", async ({ page }) => {
  await openSettings(page);
  const cf = page.getByTestId("platform-cloudflare");
  await cf.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("refused-token");
  await page.getByLabel("Type cloudflare").fill("cloudflare");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("cloudflare refused the token")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(cf).toContainText("Not connected");
});

test("disconnect asks for the platform name and removes it", async ({ page }) => {
  await openSettings(page);
  const hf = page.getByTestId("platform-hf_space");
  await hf.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("hf_x");
  await page.getByLabel("Type hf_space").fill("hf_space");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(hf).toContainText("Verified");

  await hf.getByRole("button", { name: "Disconnect" }).click();
  await page.getByLabel("Type hf_space").fill("hf_space");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(hf).toContainText("Not connected");
});

test("without the vault key nothing can be connected, and the page says what to set", async ({ page, request }) => {
  await request.post(`${gateway}/__test/reset-control?no-vault=1`);
  await openSettings(page);
  await expect(page.getByTestId("vault-missing")).toContainText("AURA_SECRETS_ENCRYPTION_KEY");
  await expect(page.getByTestId("platform-railway").getByRole("button", { name: "Connect" })).toBeDisabled();
});

test("AURA settings: secrets show only set or default, protected rows say where to change them", async ({ page }) => {
  await openSettings(page);
  await page.getByRole("link", { name: "AURA settings" }).click();
  await expect(page.getByText("AURA_KNOWLEDGE_TOP_K")).toBeVisible();
  await expect(page.getByText("Range 1 to 50")).toBeVisible();
  const database = page.locator("div").filter({ hasText: /^AURA_DATABASE_URL/ }).first();
  await expect(page.getByText("Change in the Railway dashboard")).toBeVisible();
  await expect(page.getByTestId("secret-badge").first()).toContainText("secret");
  expect(await database.count()).toBeGreaterThan(0);

  await page.getByPlaceholder("Search settings").fill("smtp");
  await expect(page.getByTestId("settings-count")).toHaveText("1 settings");
  await expect(page.getByText("AURA_KNOWLEDGE_TOP_K")).toHaveCount(0);
});

test("Arabic: the control center reads right to left with the same rules", async ({ page, context }) => {
  await signIn(page, "SuperAdmin mock login");
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByTestId("platform-hf_space")).toContainText("مش متوصّل");
  await page.getByRole("link", { name: "إعدادات AURA" }).click();
  await expect(page.getByText("AURA_KNOWLEDGE_TOP_K")).toBeVisible();
});
