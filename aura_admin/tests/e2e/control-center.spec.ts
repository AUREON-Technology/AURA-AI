import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// The Super-Admin control center, stages 1 and 2 (ADR-037, docs/plans/console-control-center-plan.md).
// One file on purpose: the mock gateway's control state is shared, and Playwright keeps one
// file's tests in order on one worker, so no other spec can reset it mid-test.
// The mock gateway enforces what aura_core/api/admin_control.py enforces: SuperAdmin only, a
// step-up grant on every write (bought with a one-time challenge), the typed confirmation, and
// tokens that are never returned.

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";
// A Cloudflare connection needs its account id (32 lower-case hexadecimal characters).
const ACCOUNT_ID = "0123456789abcdef0123456789abcdef";

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
    key_values: { hfspace: string; space: string; workersai: string; groq: string };
    cf_tokens: string[];
    grafana_loaded: string[];
    qdrant_points: Record<string, number>;
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

  // The console runs no AURA: a missing health check is only a note there, not a refusal.
  await page.getByTestId("service-console").click();
  await expect(page.getByTestId("no-healthcheck-note")).toBeVisible();
  await expect(page.getByTestId("no-healthcheck")).toHaveCount(0);
});

test("Railway: an AURA service with no health check gets the warning, and its change is refused", async ({ page, request }) => {
  await request.post(`${gateway}/__test/reset-control?no-gw-healthcheck=1`);
  await openRailway(page);
  await expect(page.getByTestId("no-healthcheck")).toContainText("Variable changes to a service that runs AURA are refused");
  await page.getByRole("button", { name: "Edit AURA_KNOWLEDGE_TOP_K" }).click();
  await page.getByLabel("New value for AURA_KNOWLEDGE_TOP_K").fill("12");
  await page.getByRole("button", { name: "Add to changes" }).click();
  await page.getByTestId("change-tray").getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("change-tray")).toContainText("has no health-check path");
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

// -- Provider keys (stage 4): the mock applies the refusals of aura_core/control/keys.py. No key
// value -- old or new -- may ever reach the page.
async function connectPlatform(page: Page, platform: string, target?: string) {
  const card = page.getByTestId(`platform-${platform}`);
  await card.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill(`${platform}-management-token`);
  const targetLabel: Record<string, string> = { hf_space: "Space (owner/name)", grafana: "Stack URL", cloudflare: "Account ID" };
  if (target) await page.getByLabel(targetLabel[platform]).fill(target);
  await page.getByLabel(`Type ${platform}`).fill(platform);
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(card).toContainText("Verified");
}

async function openKeys(page: Page, platforms: [string, string?][]) {
  await openSettings(page);
  for (const [platform, target] of platforms) await connectPlatform(page, platform, target);
  await page.getByRole("link", { name: "Keys" }).click();
  await expect(page.getByTestId("keys-panel")).toBeVisible();
}

function noKeyValues(html: string, values: string[]) {
  for (const value of ["hf-old-key-never-shown", "cf-old-key-never-shown", "groq-old-key-never-shown", ...values]) {
    expect(html).not.toContain(value);
  }
}

test("Keys: without Railway nothing can be written, and the tab says why", async ({ page }) => {
  await openKeys(page, []);
  await expect(page.getByTestId("keys-need-railway")).toContainText("Connect Railway first");
  await expect(page.getByRole("button", { name: "Replace the key for groq" })).toBeDisabled();
});

test("Keys: a pasted key replaces the gateway variable and is never shown back", async ({ page, request }) => {
  await openKeys(page, [["railway"]]);
  await expect(page.getByTestId("key-groq").getByTestId("key-health")).toHaveText("failing");
  await page.getByRole("button", { name: "Replace the key for groq" }).click();
  await page.getByLabel("New key").fill("groq-new-key-12345");
  await confirmWith(page, "groq");
  await expect(page.getByTestId("keys-notice")).toContainText("The groq key was replaced");
  expect((await seen(request)).key_values.groq).toBe("groq-new-key-12345");
  noKeyValues(await page.content(), ["groq-new-key-12345"]);
});

test("Keys: the GPU Space key rotates on both sides with one confirmation", async ({ page, request }) => {
  await openKeys(page, [["railway"], ["hf_space", "org/aura-inference"]]);
  await page.getByTestId("key-hfspace").getByRole("button", { name: "Rotate both sides" }).click();
  await expect(page.getByRole("dialog")).toContainText("VLLM_API_KEY");
  await confirmWith(page, "hfspace");
  await expect(page.getByTestId("keys-notice")).toContainText("rotated on both sides");
  const values = (await seen(request)).key_values;
  expect(values.hfspace).not.toBe("hf-old-key-never-shown");
  expect(values.space).toBe(values.hfspace);
  noKeyValues(await page.content(), [values.hfspace]);
});

test("Keys: a new Cloudflare key is created, and only the unused token can be revoked", async ({ page, request }) => {
  await openKeys(page, [["railway"], ["cloudflare", ACCOUNT_ID]]);
  await page.getByTestId("key-workersai").getByRole("button", { name: "Create new key" }).click();
  await confirmWith(page, "workersai");
  await expect(page.getByTestId("keys-notice")).toContainText("Revoke the old token");
  await expect(page.getByTestId("cf-token-aura-workersai-new")).toContainText("in use");
  await expect(page.getByRole("button", { name: "Revoke aura-workersai-new" })).toHaveCount(0);

  await page.getByRole("button", { name: "Revoke aura-workersai-old" }).click();
  await confirmWith(page, "aura-workersai-old");
  await expect(page.getByTestId("keys-notice")).toContainText("aura-workersai-old was revoked");
  const after = await seen(request);
  expect(after.cf_tokens).toEqual(["aura-workersai-new"]);
  noKeyValues(await page.content(), [after.key_values.workersai]);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Revoked the token aura-workersai-old");
});

// -- The Cloudflare tab (stage 6, C1): the connection and the account's API tokens ------------
async function openCloudflare(page: Page, platforms: [string, string?][] = []) {
  await openSettings(page);
  for (const [platform, target] of platforms) await connectPlatform(page, platform, target);
  await page.getByRole("link", { name: "Cloudflare", exact: true }).click();
  await expect(page.getByTestId("cloudflare-panel")).toBeVisible();
}

test("Cloudflare tab: not connected, it says so and shows no tokens", async ({ page }) => {
  await openCloudflare(page);
  await expect(page.getByTestId("platform-cloudflare")).toContainText("Not connected");
  await expect(page.getByTestId("cf-connect-first")).toContainText("Connect Cloudflare above");
  await expect(page.getByTestId(/^cf-token-/)).toHaveCount(0);
});

test("Cloudflare tab: connecting needs the account ID, which is shown; the token never comes back", async ({ page, request }) => {
  await openCloudflare(page);
  const card = page.getByTestId("platform-cloudflare");
  await card.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Token", { exact: true }).fill("cf-management-token-xyz");
  await page.getByLabel("Account ID").fill("not-an-account-id");
  await page.getByLabel("Type cloudflare").fill("cloudflare");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("give the Cloudflare account id")).toBeVisible();
  await expect(card).toContainText("Not connected");

  // The token is cleared after every attempt, so it is typed again with the right account ID.
  await page.getByLabel("Token", { exact: true }).fill("cf-management-token-xyz");
  await page.getByLabel("Account ID").fill(ACCOUNT_ID);
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(card).toContainText("Verified");
  await expect(card).toContainText(ACCOUNT_ID);
  await expect(page.getByTestId("cf-token-aura-workersai-old")).toBeVisible();
  await expect(page.getByTestId("cf-connect-first")).toHaveCount(0);
  expect(await page.content()).not.toContain("cf-management-token-xyz");
  expect((await seen(request)).tokens_received).toEqual(["cf-management-token-xyz"]);
});

test("Cloudflare tab: the token the gateway uses is listed and cannot be revoked", async ({ page }) => {
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  await expect(page.getByTestId("cf-token-aura-workersai-old")).toContainText("in use");
  await expect(page.getByRole("button", { name: "Revoke aura-workersai-old" })).toHaveCount(0);
  noKeyValues(await page.content(), []);
});

test("Cloudflare tab: the same tokens are on the Keys tab too", async ({ page }) => {
  await openKeys(page, [["railway"], ["cloudflare", ACCOUNT_ID]]);
  await expect(page.getByTestId("cf-token-aura-workersai-old")).toBeVisible();
});

test("Cloudflare tab: Arabic reads right to left", async ({ page, context }) => {
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings/cloudflare");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("الاتصال", { exact: true })).toBeVisible();
  await expect(page.getByTestId("platform-cloudflare").locator("code")).toHaveAttribute("dir", "ltr");
});

// -- Grafana Cloud and Qdrant Cloud (stage 5) -------------------------------------------------
async function openTab(page: Page, tab: string, platforms: [string, string?][]) {
  await openSettings(page);
  for (const [platform, target] of platforms) await connectPlatform(page, platform, target);
  await page.getByRole("link", { name: tab }).click();
}

test("Monitoring: before Grafana is connected, the tab says where to connect it", async ({ page }) => {
  await openTab(page, "Monitoring", []);
  await expect(page.getByTestId("monitoring-not-connected")).toContainText("Grafana Cloud is not connected");
});

test("Monitoring: apply loads every rule group, and the manual steps show exact values", async ({ page, request }) => {
  await openTab(page, "Monitoring", [["grafana", "https://aurastack.grafana.net"]]);
  await expect(page.getByTestId("monitoring-rules")).toContainText("not all loaded");
  const scrape = page.getByTestId("monitoring-scrape");
  await expect(scrape.getByTestId("scrape-state")).toHaveText("not set up");
  await expect(scrape).toContainText("aura-gateway");
  await expect(scrape).toContainText("https://api.example.test/metrics");
  await expect(page.getByTestId("metrics-token-missing")).toBeVisible();
  await expect(page.getByTestId("uptime-check").first()).toContainText("100%");
  await expect(page.getByTestId("monitoring-firing")).toContainText("AuraHighErrorRate");

  await page.getByRole("button", { name: "Apply setup" }).click();
  await confirmWith(page, "grafana");
  await expect(page.getByTestId("monitoring-applied")).toHaveText("Every rule group was loaded.");
  await expect(page.getByTestId("monitoring-rules")).toContainText("all loaded");
  expect((await seen(request)).grafana_loaded).toHaveLength(6);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Loaded the alert rules into Grafana Cloud");
});

test("Vector DB: a snapshot, then a restore that keeps the state it replaces", async ({ page, request }) => {
  await openTab(page, "Vector DB", [["qdrant"]]);
  await expect(page.getByTestId("vector-health")).toHaveText("healthy");
  await expect(page.getByTestId("collection-aura-knowledge")).toContainText("1,200");
  await page.getByTestId("collection-aura-knowledge").getByRole("button", { name: "Snapshots" }).click();
  await expect(page.getByTestId("snapshots")).toContainText("No snapshots yet.");

  await page.getByRole("button", { name: "Take snapshot" }).click();
  await confirmWith(page, "aura-knowledge");
  await expect(page.getByTestId("vector-notice")).toContainText("A snapshot of aura-knowledge was taken");
  await expect(page.getByTestId("snapshot-row")).toHaveCount(1);

  await page.getByRole("button", { name: /Restore from aura-knowledge-snap-1/ }).click();
  await expect(page.getByRole("dialog")).toContainText("A snapshot of the current state is taken first");
  await confirmWith(page, "aura-knowledge");
  await expect(page.getByTestId("vector-notice")).toContainText("is kept as aura-knowledge-snap-2.snapshot");
  await expect(page.getByTestId("snapshot-row")).toHaveCount(2);
  expect((await seen(request)).qdrant_points["aura-knowledge"]).toBe(1200);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Restored aura-knowledge from a snapshot");
});

test("Vector DB: Arabic reads right to left", async ({ page, context }) => {
  await openTab(page, "Vector DB", [["qdrant"]]);
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings/vector");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("الـ Collection")).toBeVisible();
});

// -- Cloudflare usage and R2 (stage 6, C3). Read-only views; the mock simulates what Cloudflare can
// answer through /__test/cloudflare-views (usage: ok | no_neurons | near_limit | no_permission |
// unavailable; r2: not_enabled | ok | empty | no_permission | unavailable). Kept at the end of the
// file, apart from the "Cloudflare tab:" tests above.
async function cloudflareViews(request: APIRequestContext, views: { usage?: string; r2?: string }) {
  await request.post(`${gateway}/__test/cloudflare-views`, { data: views });
}

test("Cloudflare usage: today's requests and neurons against the free allowance, and it is not for billing", async ({ page, request }) => {
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  const usage = page.getByTestId("cf-usage");
  await expect(usage.getByTestId("usage-requests")).toHaveText("128");
  await expect(usage.getByTestId("usage-neurons")).toHaveText("2,840.5");
  await expect(usage.getByTestId("usage-allowance")).toHaveText("2,840.5 of 10,000 neurons (free plan)");
  await expect(usage.getByTestId("usage-percent")).toHaveText("28% used");
  await expect(usage.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2840.5");
  await expect(usage).toContainText("free-plan figure");
  await expect(usage.getByTestId("usage-not-for-billing")).toContainText("not for billing");
  await expect(usage.getByTestId("usage-no-permission")).toHaveCount(0);

  await cloudflareViews(request, { usage: "near_limit" });
  await usage.getByRole("button", { name: "Refresh usage" }).click();
  await expect(usage.getByTestId("usage-neurons")).toHaveText("8,650.5");
  await expect(usage.getByTestId("usage-percent")).toHaveText("87% used");
});

test("Cloudflare usage: a token without analytics permission is said in words, never shown as zero", async ({ page, request }) => {
  await cloudflareViews(request, { usage: "no_permission" });
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  const usage = page.getByTestId("cf-usage");
  await expect(usage.getByTestId("usage-no-permission")).toContainText("Account Analytics: Read");
  await expect(usage.getByTestId("usage-requests")).toHaveCount(0);
  await expect(usage.getByRole("progressbar")).toHaveCount(0);
  await expect(usage.getByTestId("usage-not-for-billing")).toBeVisible();
});

test("Cloudflare usage: with no neuron figure it shows requests only and says so; an outage is a state", async ({ page, request }) => {
  await cloudflareViews(request, { usage: "no_neurons" });
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  const usage = page.getByTestId("cf-usage");
  await expect(usage.getByTestId("usage-requests")).toHaveText("128");
  await expect(usage.getByTestId("usage-no-neurons")).toContainText("only requests are shown");
  await expect(usage.getByTestId("usage-neurons")).toHaveCount(0);
  await expect(usage.getByRole("progressbar")).toHaveCount(0);

  await cloudflareViews(request, { usage: "unavailable" });
  await usage.getByRole("button", { name: "Refresh usage" }).click();
  await expect(usage.getByTestId("usage-unavailable")).toContainText("http_502");
  await expect(usage.getByTestId("usage-requests")).toHaveCount(0);
});

test("Cloudflare R2: not enabled on the account is a clear state, not an error", async ({ page }) => {
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  const r2 = page.getByTestId("cf-r2");
  await expect(r2.getByTestId("r2-not-enabled")).toContainText("R2 is not enabled on this account");
  await expect(r2.getByTestId(/^bucket-/)).toHaveCount(0);
  await expect(r2.getByRole("alert")).toHaveCount(0);
  await expect(r2).toContainText("Nothing here creates, changes or deletes a bucket");
});

test("Cloudflare R2: the buckets, with lock rules where Cloudflare says; a missing permission names itself", async ({ page, request }) => {
  await cloudflareViews(request, { r2: "ok" });
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  const r2 = page.getByTestId("cf-r2");
  await expect(r2.getByTestId(/^bucket-/)).toHaveCount(3);
  await expect(r2.getByTestId("bucket-aura-backups")).toContainText("WEUR");
  await expect(r2.getByTestId("bucket-aura-backups")).toContainText("1 rules");
  await expect(r2.getByTestId("bucket-aura-scratch")).toContainText("no rules");
  await expect(r2.getByTestId("bucket-aura-archive")).toContainText("unknown");
  await expect(r2.getByTestId("r2-not-enabled")).toHaveCount(0);

  await cloudflareViews(request, { r2: "no_permission" });
  await r2.getByRole("button", { name: "Refresh buckets" }).click();
  await expect(r2.getByTestId("r2-no-permission")).toContainText("Workers R2 Storage: Read");
  await expect(r2.getByTestId(/^bucket-/)).toHaveCount(0);

  await cloudflareViews(request, { r2: "empty" });
  await r2.getByRole("button", { name: "Refresh buckets" }).click();
  await expect(r2.getByTestId("r2-empty")).toHaveText("No buckets in this account.");
});

test("Cloudflare usage and R2: Arabic reads right to left and numbers and ids stay left to right", async ({ page, request, context }) => {
  await cloudflareViews(request, { r2: "ok" });
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings/cloudflare");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("استخدام Workers AI النهارده")).toBeVisible();
  await expect(page.getByTestId("usage-not-for-billing")).toContainText("مش للفوترة");
  await expect(page.getByTestId("usage-requests")).toHaveAttribute("dir", "ltr");
  await expect(page.getByTestId("usage-requests")).toHaveText("128");
  await expect(page.getByTestId("usage-allowance")).toContainText("10,000");
  await expect(page.getByText("الـ Buckets بتاعة R2 للنسخ الاحتياطي")).toBeVisible();
  await expect(page.getByTestId("bucket-aura-backups").locator("code")).toHaveAttribute("dir", "ltr");
  await expect(page.getByTestId("bucket-aura-backups")).toContainText("1 قاعدة");
});
