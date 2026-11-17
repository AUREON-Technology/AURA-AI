import { expect, test } from "@playwright/test";

// The console moved from /console on a shared host to the root of its own host
// (console.aureon-ai.tech). Old bookmarks and links keep working: next.config.ts redirects
// /console and /console/* to the same page without the prefix. The suite runs the console at
// the root, which is the configuration these redirects exist for.

test("an old /console URL is a permanent redirect to the same path without the prefix", async ({ request }) => {
  const login = await request.get("/console/login", { maxRedirects: 0 });
  expect(login.status()).toBe(308);
  expect(login.headers()["location"]).toBe("/login");

  const nested = await request.get("/console/providers?tab=1", { maxRedirects: 0 });
  expect(nested.status()).toBe(308);
  expect(nested.headers()["location"]).toBe("/providers?tab=1");

  const bare = await request.get("/console", { maxRedirects: 0 });
  expect(bare.status()).toBe(308);
  expect(bare.headers()["location"]).toBe("/");
});

test("signed out, the old console root ends on the login page", async ({ page }) => {
  await page.goto("/console");
  await expect(page).toHaveURL(/\/login$/);
});

test("signed in, an old deep link opens that page", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();

  await page.goto("/console/providers");
  await expect(page).toHaveURL(/\/providers$/);
  await expect(page.getByRole("heading", { name: "Providers", level: 1 })).toBeVisible();
});
