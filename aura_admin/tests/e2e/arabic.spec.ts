import { expect, test, type Page } from "@playwright/test";

const app = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";

async function langCookie(page: Page) {
  return (await page.context().cookies()).find((c) => c.name === "aura_console_lang")?.value;
}

// A click that lands before hydration (slow under `next dev`) does nothing, so retry until the
// toggle has written the cookie. It reloads the page as soon as it does, so no click repeats.
async function toggleTo(page: Page, lang: "en" | "ar") {
  await expect(async () => {
    await page.getByTestId("lang-toggle").click({ timeout: 2_000 });
    expect(await langCookie(page)).toBe(lang);
  }).toPass({ timeout: 20_000 });
  // The reload it started is a full server render, slow under `next dev` with other workers busy.
  await expect(page.locator("html")).toHaveAttribute("lang", lang, { timeout: 30_000 });
}

// Plan decision 12: the console has Arabic and RTL behind a language toggle. Every other spec
// runs in English, the default with no aura_console_lang cookie; this one covers Arabic.

test("the toggle switches the console to Arabic, right to left, and back", async ({ page }) => {
  await page.goto("/login");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();

  await toggleTo(page, "ar");
  const html = page.locator("html");
  await expect(html).toHaveAttribute("dir", "rtl");
  await expect(html).toHaveAttribute("lang", "ar");
  await expect(page.getByRole("heading", { name: "نظرة عامة", level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "الموافقات" })).toBeVisible();

  // Server-rendered on a full load from the cookie: no LTR first paint.
  await page.getByRole("link", { name: "الموافقات" }).click();
  await expect(page.getByRole("heading", { name: "الموافقات", level: 1 })).toBeVisible();
  const raw = await (await page.request.get(`${app}/approvals`)).text();
  expect(raw).toMatch(/<html[^>]*lang="ar"[^>]*dir="rtl"/);

  await toggleTo(page, "en");
  await expect(html).toHaveAttribute("dir", "ltr");
  await expect(html).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();
});

test("an aura_console_lang=ar cookie renders the login page in Arabic", async ({ page }) => {
  await page.context().addCookies([{ name: "aura_console_lang", value: "ar", url: app }]);
  await page.goto("/login");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.getByRole("heading", { name: "كونسول AURA", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "دخول تجريبي (Developer)" })).toBeVisible();
});
