import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// The Super-Admin control center, stages 1 and 2 (ADR-037, docs/plans/console-control-center-plan.md).
// One file on purpose: the mock gateway's control state is shared, and Playwright keeps one
// file's tests in order on one worker, so no other spec can reset it mid-test.
// The mock gateway enforces what aura_core/api/admin_control.py enforces: SuperAdmin only, a
// step-up grant on every write (bought with a one-time challenge), the typed confirmation, and
// tokens that are never returned.

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";

async function seen(request: APIRequestContext) {
  return (await (await request.get(`${gateway}/__test/control`)).json()) as {
    step_ups: number;
    tokens_received: string[];
    live_grants: number;
    railway_vars: string;
    railway_applied: string[][];
    railway_restarts: string[];
    space_events: string[];
    space_vars: Record<string, string>;
    space_secret_names: string[];
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
  await expect(page.getByTestId("platform-railway")).toContainText("Verified");
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
  await expect(page.getByTestId("platform-railway")).toContainText("Verified");
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

// -- Railway (stage 2): the mock applies the refusals of aura_core/control/railway.py and holds
// a secret (RAILWAY_SECRET in mock-gateway.mjs) that must never reach the page.
const SECRET = "rw-secret-value-never-shown";

async function connectRailway(page: Page) {
  const card = page.getByTestId("platform-railway");
  await card.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("railway-project-token");
  await page.getByLabel("Type railway").fill("railway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(card).toContainText("Verified");
}

async function openRailway(page: Page) {
  await openSettings(page);
  await connectRailway(page);
  await page.getByRole("link", { name: "Railway" }).click();
  await expect(page.getByTestId("railway-scope")).toContainText("Project aura · environment production");
}

test("Railway: before Railway is connected, the tab says where to connect it", async ({ page }) => {
  await openSettings(page);
  await page.getByRole("link", { name: "Railway" }).click();
  await expect(page.getByTestId("railway-not-connected")).toContainText("Railway is not connected");
});

test("Railway: services, variables and logs: secrets never reach the page", async ({ page }) => {
  await openRailway(page);
  await expect(page.getByTestId("service-gateway")).toHaveAttribute("aria-selected", "true");
  const panel = page.getByTestId("service-panel");
  await expect(panel).toContainText("this gateway");
  await expect(page.getByTestId("var-AURA_KNOWLEDGE_TOP_K")).toContainText("4");
  await expect(page.getByTestId("var-AURA_JWT_SECRET").getByTestId("secret-badge")).toBeVisible();
  await expect(page.getByTestId("var-AURA_DATABASE_URL")).toContainText("Change in the Railway dashboard");
  await expect(page.getByTestId("var-RAILWAY_SERVICE_NAME")).toContainText("provided by Railway");
  await expect(page.getByRole("button", { name: "Edit AURA_DATABASE_URL" })).toHaveCount(0);

  await page.getByRole("button", { name: "Logs for dep-2" }).click();
  await expect(page.getByTestId("logs")).toContainText("jwt loaded [redacted]");
  expect(await page.content()).not.toContain(SECRET);

  await page.getByTestId("service-console").click();
  await expect(page.getByTestId("no-healthcheck")).toBeVisible();
});

test("Railway: a variable change is checked, applied behind the service name, and watched", async ({ page, request }) => {
  await openRailway(page);
  await page.getByRole("button", { name: "Edit AURA_KNOWLEDGE_TOP_K" }).click();
  await page.getByLabel("New value for AURA_KNOWLEDGE_TOP_K").fill("12");
  await page.getByRole("button", { name: "Add to changes" }).click();

  const tray = page.getByTestId("change-tray");
  await expect(tray).toContainText("Pending changes to gateway: 1");
  await expect(tray).toContainText("→ 12");
  await tray.getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("tray-checked")).toBeVisible();
  expect((await seen(request)).railway_vars).toBe("4");

  await tray.getByRole("button", { name: "Apply" }).click();
  await page.getByLabel("Type gateway").fill("gateway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("railway-notice")).toContainText("watched for 5 minutes");
  await expect(page.getByTestId("change-tray")).toHaveCount(0);
  expect((await seen(request)).railway_vars).toBe("12");
  await expect(page.getByTestId("deployment-row").first()).toContainText("DEPLOYING");
  await expect(page.getByTestId("service-panel")).toContainText("change being watched");

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Changed variables on gateway: AURA_KNOWLEDGE_TOP_K");
});

test("Railway: a value the gateway could not boot with is refused at the check, and nothing is written", async ({ page, request }) => {
  await openRailway(page);
  await page.getByRole("button", { name: "Edit AURA_KNOWLEDGE_TOP_K" }).click();
  await page.getByLabel("New value for AURA_KNOWLEDGE_TOP_K").fill("999");
  await page.getByRole("button", { name: "Add to changes" }).click();
  await page.getByTestId("change-tray").getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("change-tray")).toContainText("AURA_KNOWLEDGE_TOP_K must be <= 50");
  expect((await seen(request)).railway_applied).toEqual([]);
});

test("Railway: a new secret variable is typed hidden and never shown back", async ({ page, request }) => {
  await openRailway(page);
  await page.getByLabel("New variable").fill("AURA_NEW_API_TOKEN");
  const value = page.getByLabel("Value", { exact: true });
  await expect(value).toHaveAttribute("type", "password");
  await value.fill("typed-secret-12345");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByTestId("change-tray")).toContainText("new secret value set");
  expect(await page.content()).not.toContain("typed-secret-12345");

  await page.getByTestId("change-tray").getByRole("button", { name: "Apply" }).click();
  await page.getByLabel("Type gateway").fill("gateway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("railway-notice")).toBeVisible();
  await expect(page.getByTestId("var-AURA_NEW_API_TOKEN").getByTestId("secret-badge")).toBeVisible();
  expect(await page.content()).not.toContain("typed-secret-12345");
  expect((await seen(request)).railway_applied).toEqual([["AURA_NEW_API_TOKEN"]]);
});

test("Railway: redeploy, restart and rollback each ask for the service name", async ({ page, request }) => {
  await openRailway(page);
  await page.getByRole("button", { name: "Restart", exact: true }).click();
  await page.getByLabel("Type gateway").fill("gateway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("railway-notice")).toContainText("Restart requested");
  expect((await seen(request)).railway_restarts).toEqual(["dep-2"]);

  await page.getByRole("button", { name: "Roll back to dep-1" }).click();
  await page.getByLabel("Type gateway").fill("wrong");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("Type gateway").fill("gateway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("railway-notice")).toContainText("Rollback started");

  await page.getByRole("button", { name: "Redeploy", exact: true }).click();
  await page.getByLabel("Type gateway").fill("gateway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("railway-notice")).toContainText("Redeploy started");
});

test("Railway: AURA settings become editable once Railway is connected", async ({ page, request }) => {
  await openSettings(page);
  await page.getByRole("link", { name: "AURA settings" }).click();
  await expect(page.getByTestId("settings-source")).toContainText("Read-only");
  await expect(page.getByRole("button", { name: "Edit AURA_KNOWLEDGE_TOP_K" })).toHaveCount(0);

  // The sidebar has its own "Overview": use the Settings tabs.
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Overview" }).click();
  await connectRailway(page);
  await page.getByRole("link", { name: "AURA settings" }).click();
  await expect(page.getByTestId("settings-source")).toContainText("gateway");
  await expect(page.getByRole("button", { name: "Edit AURA_DATABASE_URL" })).toHaveCount(0);
  await page.getByRole("button", { name: "Edit AURA_KNOWLEDGE_TOP_K" }).click();
  await page.getByLabel("New value for AURA_KNOWLEDGE_TOP_K").fill("7");
  await page.getByRole("button", { name: "Add to changes" }).click();
  await page.getByTestId("change-tray").getByRole("button", { name: "Apply" }).click();
  await page.getByLabel("Type gateway").fill("gateway");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("settings-notice")).toBeVisible();
  expect((await seen(request)).railway_vars).toBe("7");
  await expect(page.getByTestId("setting-AURA_KNOWLEDGE_TOP_K").getByTestId("next-deploy")).toContainText("7");
});

test("Railway: Arabic: the Railway tab reads right to left", async ({ page, context }) => {
  await openRailway(page);
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings/railway");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByTestId("service-panel")).toContainText("الـ Gateway ده");
  await expect(page.getByText("المتغيرات")).toBeVisible();
});

// -- GPU Space (stage 3): the mock applies the refusals of aura_core/control/space.py and holds a
// Space secret (SPACE_SECRET in mock-gateway.mjs) that must never reach the page.
const SPACE_SECRET = "hf-space-secret-never-shown";

async function openSpace(page: Page) {
  await openSettings(page);
  const card = page.getByTestId("platform-hf_space");
  await card.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("hf_token_value");
  await page.getByLabel("Space (owner/name)").fill("org/aura-inference");
  await page.getByLabel("Type hf_space").fill("hf_space");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(card).toContainText("Verified");
  await page.getByRole("link", { name: "GPU Space" }).click();
  await expect(page.getByTestId("space-stage")).toHaveText("RUNNING");
}

async function confirmWith(page: Page, word: string) {
  await page.getByLabel(`Type ${word}`).fill(word);
  await page.getByRole("button", { name: "Confirm" }).click();
}

test("GPU Space: before it is connected, the tab says where to connect it", async ({ page }) => {
  await openSettings(page);
  await page.getByRole("link", { name: "GPU Space" }).click();
  await expect(page.getByTestId("space-not-connected")).toContainText("The GPU Space is not connected");
});

test("GPU Space: status, secrets by name only, and logs", async ({ page }) => {
  await openSpace(page);
  const panel = page.getByTestId("space-panel");
  await expect(panel).toContainText("org/aura-inference");
  await expect(panel).toContainText("Hardware l4x1");
  await expect(page.getByTestId("space-var-MODEL_ID")).toContainText("Qwen/Qwen2.5-7B-Instruct-AWQ");
  await expect(page.getByTestId("space-secret-HF_TOKEN")).toBeVisible();
  await expect(page.getByTestId("space-logs")).toContainText("vLLM engine started");
  expect(await page.content()).not.toContain(SPACE_SECRET);
});

test("GPU Space: a variable change is checked, applied behind the Space name, and watched", async ({ page, request }) => {
  await openSpace(page);
  await page.getByRole("button", { name: "Edit MAX_MODEL_LEN" }).click();
  await page.getByLabel("New value for MAX_MODEL_LEN").fill("4096");
  await page.getByRole("button", { name: "Add to changes" }).click();
  const tray = page.getByTestId("change-tray");
  await tray.getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("tray-checked")).toBeVisible();
  await tray.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("dialog")).toContainText("within 20 minutes");
  await confirmWith(page, "aura-inference");
  await expect(page.getByTestId("space-notice")).toContainText("watched for 20 minutes");
  await expect(page.getByTestId("space-stage")).toHaveText("APP_STARTING");
  expect((await seen(request)).space_vars.MAX_MODEL_LEN).toBe("4096");
});

test("GPU Space: a secret-looking variable is refused at the check", async ({ page, request }) => {
  await openSpace(page);
  await page.getByLabel("New variable").fill("OPENAI_API_KEY");
  await page.getByLabel("Value", { exact: true }).fill("sk-not-here");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByTestId("change-tray").getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("change-tray")).toContainText("looks secret");
  expect((await seen(request)).space_events).toEqual([]);
});

test("GPU Space: a secret is replaced write-only, with a warning that it cannot be undone", async ({ page, request }) => {
  await openSpace(page);
  await page.getByRole("button", { name: "Replace HF_TOKEN" }).click();
  await expect(page.getByRole("dialog")).toContainText("cannot be undone automatically");
  await page.getByLabel("New value").fill("hf_new_secret_value_42");
  await confirmWith(page, "aura-inference");
  await expect(page.getByTestId("space-notice")).toContainText("HF_TOKEN saved");
  expect(await page.content()).not.toContain("hf_new_secret_value_42");
  expect((await seen(request)).space_events).toEqual(["secrets"]);
});

test("GPU Space: pause, resume, hardware and sleep time each ask for the Space name", async ({ page, request }) => {
  await openSpace(page);
  await page.getByRole("button", { name: "Pause" }).click();
  await confirmWith(page, "aura-inference");
  await expect(page.getByTestId("space-stage")).toHaveText("PAUSED");
  await page.getByRole("button", { name: "Resume" }).click();
  await confirmWith(page, "aura-inference");
  await expect(page.getByTestId("space-stage")).toHaveText("APP_STARTING");

  await page.getByLabel("Hardware").selectOption("a10g-large");
  await page.getByRole("button", { name: "Change hardware" }).click();
  await expect(page.getByRole("dialog")).toContainText("billed by Hugging Face per hour");
  await confirmWith(page, "aura-inference");
  await expect(page.getByTestId("space-notice")).toContainText("a10g-large requested");

  await page.getByLabel("Sleep after inactivity").selectOption("900");
  await page.getByRole("button", { name: "Change sleep time" }).click();
  await confirmWith(page, "aura-inference");
  await expect(page.getByTestId("space-notice")).toContainText("Sleep time changed");
  expect((await seen(request)).space_events).toEqual(["pause", "restart", "hardware:a10g-large", "sleep:900"]);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Changed when aura-inference sleeps");
});

test("GPU Space: Arabic reads right to left", async ({ page, context }) => {
  await openSpace(page);
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings/space");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("الـ Secrets")).toBeVisible();
});
