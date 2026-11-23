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

// What the Cloudflare providers (C2) wrote to the gateway: the variables behind them, the keys a
// person pasted and the Cloudflare tokens that exist. The API never returns a key; the mock's test
// hook does, so a test can prove where a value went and where it did not.
async function providerState(request: APIRequestContext) {
  return (await (await request.get(`${gateway}/__test/control`)).json()) as {
    provider_vars: Record<string, string>;
    pasted_keys: string[];
    cf_tokens: string[];
    step_ups: number;
  };
}

/** The end of the five-minute watch: watched changes succeed, and a removed provider's token is revoked. */
async function finishChanges(request: APIRequestContext) {
  await request.post(`${gateway}/__test/finish-changes`);
}

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

// -- The Cloudflare tab (stage 6, C2): the model catalog and the providers the gateway serves from.
// The mock keeps providers as gateway variables and applies the refusals of control/cloudflare.py;
// a change stays "watched" (and blocks the next one) until the test ends the watch.
async function openCloudflareProviders(page: Page) {
  await openCloudflare(page, [["railway"], ["cloudflare", ACCOUNT_ID]]);
  await expect(page.getByTestId("cf-model-@cf/openai/gpt-oss-20b")).toBeVisible();
}

async function openAdd(page: Page, model: string) {
  await page.getByRole("button", { name: `Add ${model}` }).click();
  const dialog = page.getByTestId("provider-dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

const CONSENT = "I understand, and I consent to a third party reading prompts";

/** The whole Add flow for one model, then the end of the watch so the next change is allowed. */
async function addProvider(page: Page, request: APIRequestContext, model: string, name: string, rpm = "0") {
  const dialog = await openAdd(page, model);
  await dialog.getByLabel("Provider name").fill(name);
  await dialog.getByLabel("Requests per minute").fill(rpm);
  if (await dialog.getByTestId("consent-box").count()) await dialog.getByLabel(CONSENT).check();
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(dialog.getByTestId("provider-verdict")).toContainText("The gateway accepts this change");
  await dialog.getByRole("button", { name: "Add provider" }).click();
  await confirmWith(page, name);
  await expect(page.getByTestId("cloudflare-notice")).toContainText(`${name} was added.`);
  await finishChanges(request);
}

test("Cloudflare providers: nothing renders before Cloudflare is connected", async ({ page }) => {
  await openCloudflare(page);
  await expect(page.getByTestId("cf-providers")).toHaveCount(0);
  await expect(page.getByTestId("cf-models")).toHaveCount(0);
});

test("Cloudflare providers: without Railway the section says so and no model can be added", async ({ page }) => {
  await openCloudflare(page, [["cloudflare", ACCOUNT_ID]]);
  await expect(page.getByTestId("cf-providers-need-railway")).toContainText("Connect Railway first");
  await expect(page.getByTestId("cf-models-need-railway")).toBeVisible();
  // The catalog needs only the account, so the models are listed, but Add is off.
  await expect(page.getByTestId("cf-model-@cf/openai/gpt-oss-20b")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add @cf/openai/gpt-oss-20b" })).toBeDisabled();
});

test("Cloudflare models: the live catalog, its filters, its pages, and why a model is refused", async ({ page }) => {
  await openCloudflareProviders(page);
  const models = page.getByTestId("cf-models");
  const rows = models.getByTestId(/^cf-model-/);

  const usable = models.getByTestId("cf-model-@cf/openai/gpt-oss-20b");
  await expect(usable).toContainText("128,000");
  await expect(usable).toContainText("Reasoning");
  await expect(usable).toContainText("Function calling");
  await expect(usable).toContainText("0.2 USD per M input tokens");
  await expect(usable).toContainText("Can be added");
  await expect(models.getByTestId("cf-model-@cf/meta/llama-3.2-11b-vision-instruct")).toContainText("Vision");
  await expect(models.getByTestId("cf-model-@cf/meta/llama-4-scout-17b-16e-instruct")).toContainText("Paid plan only");

  // A model AURA may not serve says why, in words, and has no Add button.
  await expect(models.getByTestId("cf-model-@cf/cloudflare/clairic")).toContainText("not on AURA's open-weight list");
  await expect(page.getByRole("button", { name: "Add @cf/cloudflare/clairic" })).toHaveCount(0);
  // The default task is Text Generation: the embedding model is not listed until the task is changed.
  await expect(models.getByTestId("cf-model-@cf/baai/bge-m3")).toHaveCount(0);
  await models.getByLabel("Task").selectOption("Text Embeddings");
  await expect(models.getByTestId("cf-model-@cf/baai/bge-m3")).toContainText("not a text-generation model");
  await expect(page.getByRole("button", { name: "Add @cf/baai/bge-m3" })).toHaveCount(0);
  await models.getByLabel("Task").selectOption("Text Generation");

  // 26 text-generation models, 20 a page.
  const pager = models.getByTestId("cf-models-pager");
  await expect(pager).toContainText("Page 1 of 2");
  await expect(pager).toContainText("26 models");
  await expect(rows).toHaveCount(20);
  await pager.getByRole("button", { name: "Next" }).click();
  await expect(pager).toContainText("Page 2 of 2");
  await expect(rows).toHaveCount(6);
  await expect(pager.getByRole("button", { name: "Next" })).toBeDisabled();
  await pager.getByRole("button", { name: "Previous" }).click();
  await expect(pager).toContainText("Page 1 of 2");

  await models.getByLabel("Vision only").check();
  await expect(rows).toHaveCount(1);
  await models.getByLabel("Vision only").uncheck();
  await models.getByLabel("AURA can use").check();
  await expect(models.getByTestId("cf-model-@cf/cloudflare/clairic")).toHaveCount(0);
  await models.getByLabel("AURA can use").uncheck();
  await models.getByLabel("Plan").selectOption("paid");
  await expect(rows).toHaveCount(1);
  await expect(models.getByTestId("cf-model-@cf/meta/llama-4-scout-17b-16e-instruct")).toBeVisible();
  await models.getByLabel("Plan").selectOption("any");

  await models.getByLabel("Search models").fill("gpt-oss");
  await expect(rows).toHaveCount(1);
  await expect(usable).toBeVisible();
  await models.getByLabel("Search models").fill("no-such-model");
  await expect(models).toContainText("No models match.");
});

test("Cloudflare providers: add with a created key, after a check and the consent", async ({ page, request }) => {
  await openCloudflareProviders(page);
  await expect(page.getByTestId("cf-providers")).toContainText("No Cloudflare providers on the gateway yet");

  const dialog = await openAdd(page, "@cf/openai/gpt-oss-20b");
  // The model, a suggested name and the catalog's context window are filled in; the key is created.
  await expect(dialog.getByLabel("Model", { exact: true })).toHaveValue("@cf/openai/gpt-oss-20b");
  await expect(dialog.getByLabel("Provider name")).toHaveValue("cf-gpt-oss-20b");
  await expect(dialog.getByLabel("Context window (tokens)")).toHaveValue("128000");
  await expect(dialog.getByLabel("Create a new key")).toBeChecked();
  await expect(dialog.getByRole("button", { name: "Add provider" })).toBeDisabled();
  await expect(dialog.getByTestId("consent-box")).toContainText("a third party outside this platform");

  // Without the consent the gateway refuses, in words, and nothing is written.
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(dialog.getByTestId("provider-problem")).toContainText("Hosted inference is off on the gateway");
  await expect(dialog.getByRole("button", { name: "Add provider" })).toBeDisabled();
  expect((await providerState(request)).provider_vars).toEqual({});

  await dialog.getByLabel(CONSENT).check();
  await dialog.getByLabel("Requests per minute").fill("30");
  await dialog.getByLabel("Tokens per minute").fill("6000");
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  const verdict = dialog.getByTestId("provider-verdict");
  await expect(verdict).toContainText("The gateway accepts this change");
  await expect(verdict).toContainText("A new Cloudflare token will be created");
  await expect(verdict).toContainText("AURA_ALLOW_HOSTED_INFERENCE will be set to true");
  await expect(verdict.getByTestId("verdict-variable").filter({ hasText: "AURA_PROVIDER_CF_GPT_OSS_20B_API_KEY" })).toContainText("secret");
  await expect(verdict).toContainText("AURA_PROVIDER_CF_GPT_OSS_20B_HEALTH_PROBE");
  await expect(dialog.getByRole("button", { name: "Add provider" })).toBeEnabled();

  // A form that changed since the check is not applied on the strength of it.
  await dialog.getByLabel("Requests per day").fill("1000");
  await expect(verdict).toContainText("The form changed since the last check");
  await expect(dialog.getByRole("button", { name: "Add provider" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(verdict).toContainText("The gateway accepts this change");

  await dialog.getByRole("button", { name: "Add provider" }).click();
  await confirmWith(page, "cf-gpt-oss-20b");
  const notice = page.getByTestId("cloudflare-notice");
  await expect(notice).toContainText("cf-gpt-oss-20b was added.");
  await expect(notice).toContainText("rolled back automatically");
  await expect(page.getByTestId("provider-dialog")).toHaveCount(0);

  const row = page.getByTestId("cf-provider-cf-gpt-oss-20b");
  await expect(row).toContainText("@cf/openai/gpt-oss-20b");
  await expect(row).toContainText("Healthy");
  await expect(row).toContainText("Set");
  await expect(row).toContainText("On");
  await expect(row.getByTestId("cf-limit-rpm")).toContainText("30 requests/min");
  await expect(row.getByTestId("cf-limit-rpd")).toContainText("1,000 requests/day");
  await expect(row.getByTestId("cf-limit-tpm")).toContainText("6,000 tokens/min");
  await expect(row).toContainText("passive probe");
  await expect(page.getByTestId("cf-passive-note")).toBeVisible();

  const state = await providerState(request);
  expect(state.provider_vars.AURA_LLM_PROVIDERS).toBe("hfspace,cf-gpt-oss-20b,local");
  expect(state.provider_vars.AURA_ALLOW_HOSTED_INFERENCE).toBe("true");
  expect(state.provider_vars.AURA_PROVIDER_CF_GPT_OSS_20B_RPM).toBe("30");
  expect(state.provider_vars.AURA_PROVIDER_CF_GPT_OSS_20B_HEALTH_PROBE).toBe("passive");
  expect(state.cf_tokens).toContain("aura-cf-gpt-oss-20b-20261123-1200");
  noKeyValues(await page.content(), [state.provider_vars.AURA_PROVIDER_CF_GPT_OSS_20B_API_KEY]);

  await page.getByTestId("cloudflare-changes-link").click();
  await expect(page.getByTestId("change-row").first()).toContainText("Added the Cloudflare provider cf-gpt-oss-20b");
});

test("Cloudflare providers: a pasted key reaches the gateway and never the page or a check", async ({ page, request }) => {
  await openCloudflareProviders(page);
  const checks: string[] = [];
  page.on("request", (req) => {
    if (req.url().endsWith("/providers/validate")) checks.push(req.postData() ?? "");
  });

  const dialog = await openAdd(page, "@cf/meta/llama-3.2-11b-vision-instruct");
  await expect(dialog.getByLabel("The model accepts images")).toBeChecked();
  await expect(dialog.getByTestId("vision-note")).toContainText("does not send user images to third-party providers");
  await dialog.getByLabel("Provider name").fill("cf-vision");
  await dialog.getByLabel(CONSENT).check();
  await dialog.getByLabel("Paste an existing key").check();
  const secret = "pasted-secret-key-98765";
  const field = dialog.locator('input[type="password"]');
  await field.fill(secret);
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(dialog.getByTestId("provider-verdict")).toContainText("The gateway accepts this change");
  await expect(dialog.getByTestId("provider-verdict")).not.toContainText("A new Cloudflare token");
  await dialog.getByRole("button", { name: "Add provider" }).click();
  await confirmWith(page, "cf-vision");
  await expect(page.getByTestId("cloudflare-notice")).toContainText("cf-vision was added.");

  const state = await providerState(request);
  expect(state.pasted_keys).toEqual([secret]);
  expect(state.provider_vars.AURA_PROVIDER_CF_VISION_API_KEY).toBe(secret);
  expect(state.provider_vars.AURA_PROVIDER_CF_VISION_MODALITIES).toBe("text,image");
  expect(state.cf_tokens).toEqual(["aura-workersai-old"]);
  expect(await page.content()).not.toContain(secret);
  // The check never carried the key, and its numbers were numbers: the gateway's body is strict.
  expect(checks.length).toBeGreaterThan(0);
  for (const body of checks) expect(body).not.toContain(secret);
  const sent = JSON.parse(checks[0]) as Record<string, unknown>;
  for (const numeric of ["context_window", "quality_tier", "max_output_tokens", "rpm", "rpd", "tpm", "tpd", "tokens_per_second"]) {
    expect(typeof sent[numeric]).toBe("number");
  }

  // Removing a provider whose key was pasted revokes no Cloudflare token, and says so.
  await finishChanges(request);
  await page.getByRole("button", { name: "Remove cf-vision" }).click();
  await expect(page.getByTestId("remove-token-note")).toContainText("No Cloudflare token will be revoked");
});

test("Cloudflare providers: the consent box shows only while hosted inference is off; a watched change blocks the next in words", async ({ page, request }) => {
  await openCloudflareProviders(page);
  const first = await openAdd(page, "@cf/openai/gpt-oss-20b");
  await expect(first.getByTestId("consent-box")).toBeVisible();
  await first.getByLabel(CONSENT).check();
  await first.getByRole("button", { name: "Check", exact: true }).click();
  await expect(first.getByTestId("provider-verdict")).toContainText("AURA_ALLOW_HOSTED_INFERENCE will be set to true");
  await first.getByRole("button", { name: "Add provider" }).click();
  await confirmWith(page, "cf-gpt-oss-20b");
  await expect(page.getByTestId("cloudflare-notice")).toContainText("cf-gpt-oss-20b was added.");

  // The flag is on now: no consent box, and the check does not touch it.
  const second = await openAdd(page, "@cf/meta/llama-3.2-11b-vision-instruct");
  await expect(second.getByTestId("consent-box")).toHaveCount(0);
  await second.getByLabel("Provider name").fill("cf-vision");
  await second.getByRole("button", { name: "Check", exact: true }).click();
  await expect(second.getByTestId("provider-verdict")).toContainText("The gateway accepts this change");
  await expect(second.getByTestId("provider-verdict")).not.toContainText("AURA_ALLOW_HOSTED_INFERENCE");

  // The first change is still being watched: the gateway refuses the second, and says why.
  await second.getByRole("button", { name: "Add provider" }).click();
  await confirmWith(page, "cf-vision");
  await expect(page.getByRole("dialog", { name: "Add cf-vision" }).getByRole("alert")).toContainText(
    "Another change to the gateway is still being watched",
  );
  expect((await providerState(request)).provider_vars.AURA_LLM_PROVIDERS).toBe("hfspace,cf-gpt-oss-20b,local");

  await finishChanges(request);
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("cloudflare-notice")).toContainText("cf-vision was added.");
  expect((await providerState(request)).provider_vars.AURA_LLM_PROVIDERS).toBe("hfspace,cf-gpt-oss-20b,cf-vision,local");
});

test("Cloudflare providers: refusals read in words in the form", async ({ page, request }) => {
  await openCloudflareProviders(page);
  await page.getByRole("button", { name: "Add provider" }).click();
  const dialog = page.getByTestId("provider-dialog");
  await dialog.getByLabel(CONSENT).check();

  // A model that is not open weight (the model list does not offer it, but the field can be typed in).
  await dialog.getByLabel("Model", { exact: true }).fill("@cf/cloudflare/clairic");
  await dialog.getByLabel("Provider name").fill("cf-clairic");
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(dialog.getByTestId("provider-problem")).toContainText("not on AURA's open-weight list");

  // A reserved name is refused by the spec, in the gateway's own words.
  await dialog.getByLabel("Model", { exact: true }).fill("@cf/openai/gpt-oss-20b");
  await dialog.getByLabel("Provider name").fill("local");
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(dialog.getByTestId("provider-problem")).toContainText("is not 'local' or 'echo'");

  // A number that is not a number is caught before any request.
  await dialog.getByLabel("Provider name").fill("cf-dup");
  await dialog.getByLabel("Context window (tokens)").fill("lots");
  await expect(dialog.getByTestId("numbers-hint")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Check", exact: true })).toBeDisabled();
  await dialog.getByLabel("Context window (tokens)").fill("32768");

  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  await expect(dialog.getByTestId("provider-verdict")).toContainText("The gateway accepts this change");
  await dialog.getByRole("button", { name: "Add provider" }).click();
  await confirmWith(page, "cf-dup");
  await expect(page.getByTestId("cloudflare-notice")).toContainText("cf-dup was added.");
  await finishChanges(request);

  // The same name again.
  await page.getByRole("button", { name: "Add provider" }).click();
  const again = page.getByTestId("provider-dialog");
  await again.getByLabel("Model", { exact: true }).fill("@cf/openai/gpt-oss-20b");
  await again.getByLabel("Provider name").fill("cf-dup");
  await again.getByRole("button", { name: "Check", exact: true }).click();
  await expect(again.getByTestId("provider-problem")).toContainText("A provider with this name is already on the gateway");
  await expect(again.getByRole("button", { name: "Add provider" })).toBeDisabled();
});

test("Cloudflare providers: replace changes the model and the limits, and can create a new key", async ({ page, request }) => {
  await openCloudflareProviders(page);
  await addProvider(page, request, "@cf/openai/gpt-oss-20b", "cf-gpt-oss-20b", "30");

  await page.getByRole("button", { name: "Replace cf-gpt-oss-20b" }).click();
  const dialog = page.getByTestId("provider-dialog");
  await expect(dialog).toContainText("Replace cf-gpt-oss-20b");
  await expect(dialog.getByTestId("consent-box")).toHaveCount(0);
  await expect(dialog.getByLabel("Provider name")).toBeDisabled();
  await expect(dialog.getByLabel("Keep the current key")).toBeChecked();
  await expect(dialog.getByLabel("Requests per minute")).toHaveValue("30");

  await dialog.getByLabel("Model", { exact: true }).fill("@cf/openai/gpt-oss-120b");
  await dialog.getByLabel("Requests per minute").fill("0");
  await dialog.getByLabel("Quality tier").selectOption("3");
  await dialog.getByLabel("Create a new key").check();
  await dialog.getByRole("button", { name: "Check", exact: true }).click();
  const verdict = dialog.getByTestId("provider-verdict");
  await expect(verdict).toContainText("A new Cloudflare token will be created");
  // A limit that is no longer declared is deleted, never written as 0.
  await expect(verdict.getByTestId("verdict-variable").filter({ hasText: "AURA_PROVIDER_CF_GPT_OSS_20B_RPM" })).toContainText("deleted");

  await dialog.getByRole("button", { name: "Replace provider" }).click();
  await confirmWith(page, "cf-gpt-oss-20b");
  await expect(page.getByTestId("cloudflare-notice")).toContainText("cf-gpt-oss-20b was replaced.");
  const row = page.getByTestId("cf-provider-cf-gpt-oss-20b");
  await expect(row).toContainText("@cf/openai/gpt-oss-120b");
  await expect(row.getByTestId("cf-limit-rpm")).toHaveCount(0);

  const state = await providerState(request);
  expect(state.provider_vars.AURA_PROVIDER_CF_GPT_OSS_20B_MODEL).toBe("@cf/openai/gpt-oss-120b");
  expect(state.provider_vars.AURA_PROVIDER_CF_GPT_OSS_20B_QUALITY_TIER).toBe("3");
  expect(state.provider_vars).not.toHaveProperty("AURA_PROVIDER_CF_GPT_OSS_20B_RPM");
  expect(state.cf_tokens).toContain("aura-cf-gpt-oss-20b-20261123-1201");
  noKeyValues(await page.content(), [state.provider_vars.AURA_PROVIDER_CF_GPT_OSS_20B_API_KEY]);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Replaced the Cloudflare provider cf-gpt-oss-20b");
});

test("Cloudflare providers: remove lists what goes, refuses a wrong name, and revokes the token only after the watch", async ({ page, request }) => {
  await openCloudflareProviders(page);
  await addProvider(page, request, "@cf/openai/gpt-oss-20b", "cf-gpt-oss-20b", "30");
  expect((await providerState(request)).cf_tokens).toContain("aura-cf-gpt-oss-20b-20261123-1200");

  await page.getByRole("button", { name: "Remove cf-gpt-oss-20b" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("remove-body")).toContainText("AURA_PROVIDER_CF_GPT_OSS_20B_MODEL");
  await expect(dialog.getByTestId("remove-body")).toContainText("AURA_PROVIDER_CF_GPT_OSS_20B_API_KEY");
  expect(await dialog.getByTestId("remove-variable").count()).toBeGreaterThan(10);
  await expect(dialog.getByTestId("remove-token-note")).toContainText("revoked automatically after the change has stayed healthy for 5 minutes");
  await expect(dialog.getByTestId("remove-token-note")).toContainText("If the change is rolled back, the token is not revoked");

  // A wrong name is refused before anything is sent.
  await page.getByLabel("Type cf-gpt-oss-20b").fill("cf-wrong");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Type cf-gpt-oss-20b to confirm");
  expect((await providerState(request)).provider_vars.AURA_LLM_PROVIDERS).toContain("cf-gpt-oss-20b");

  await page.getByLabel("Type cf-gpt-oss-20b").fill("cf-gpt-oss-20b");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("cloudflare-notice")).toContainText("cf-gpt-oss-20b was removed.");
  await expect(page.getByTestId("cf-provider-cf-gpt-oss-20b")).toHaveCount(0);
  const after = await providerState(request);
  expect(after.provider_vars.AURA_LLM_PROVIDERS).toBe("hfspace,local");
  expect(Object.keys(after.provider_vars).filter((name) => name.startsWith("AURA_PROVIDER_CF_GPT_OSS_20B"))).toEqual([]);
  // The token is revoked only once the change has stayed healthy, never at the click.
  expect(after.cf_tokens).toContain("aura-cf-gpt-oss-20b-20261123-1200");
  await finishChanges(request);
  expect((await providerState(request)).cf_tokens).toEqual(["aura-workersai-old"]);

  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("change-row").first()).toContainText("Removed the Cloudflare provider cf-gpt-oss-20b");
});

test("Cloudflare providers: Arabic reads right to left, with model ids and numbers left to right", async ({ page, context, request }) => {
  await openCloudflareProviders(page);
  await addProvider(page, request, "@cf/openai/gpt-oss-20b", "cf-gpt-oss-20b", "30");
  await context.addCookies([{ name: "aura_console_lang", value: "ar", url: process.env.PLAYWRIGHT_BASE_URL! }]);
  await page.goto("/settings/cloudflare");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByTestId("cf-providers")).toContainText("الـ Providers");
  await expect(page.getByTestId("cf-models")).toContainText("الموديلات");

  const row = page.getByTestId("cf-provider-cf-gpt-oss-20b");
  await expect(row.locator("code").first()).toHaveAttribute("dir", "ltr");
  await expect(row.getByTestId("cf-limit-rpm").locator("bdi")).toHaveAttribute("dir", "ltr");
  await expect(page.getByTestId("cf-model-@cf/openai/gpt-oss-20b").locator("code")).toHaveAttribute("dir", "ltr");
  await expect(page.getByTestId("cf-model-@cf/cloudflare/clairic")).toContainText("مش في قايمة الموديلات المفتوحة الأوزان");

  await page.getByRole("button", { name: "غيّر cf-gpt-oss-20b" }).click();
  const dialog = page.getByTestId("provider-dialog");
  await expect(dialog).toContainText("غيّر cf-gpt-oss-20b");
  await expect(dialog.getByLabel("الموديل", { exact: true })).toHaveAttribute("dir", "ltr");
  await expect(dialog.getByLabel("الطلبات في الدقيقة")).toHaveAttribute("dir", "ltr");
  await expect(dialog.getByTestId("vision-note")).toContainText("AURA مبتبعتش صور المستخدمين");
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
