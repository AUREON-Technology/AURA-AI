import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// SaaS P1-F3: the Customers page and its one write, "Extend grace" (spec §5.4 "Owner tooling";
// aura_core/api/billing.py, both routes SuperAdmin-only). The mock pages at 3 rows, so its four
// customers take two pages.

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";

async function calls(request: APIRequestContext) {
  return (await (await request.get(`${gateway}/__test/customer-lists`)).json()) as { calls: number; grace_calls: number };
}

test.beforeEach(async ({ request }) => {
  await request.post(`${gateway}/__test/reset-customers`);
});

async function openAsSuper(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "SuperAdmin mock login" }).click();
  await expect(page.getByText("SuperAdmin").first()).toBeVisible();
  await page.getByRole("link", { name: "Customers" }).click();
  await expect(page.getByRole("heading", { name: "Customers", level: 1 })).toBeVisible();
}

async function extend(page: Page, workspace: string, days: string) {
  await page.getByRole("button", { name: `Extend grace for ${workspace}` }).click();
  await page.getByLabel("Days of grace").fill(days);
  await page.getByLabel("Type extend").fill("extend");
  await page.getByRole("button", { name: "Confirm" }).click();
}

test("a SuperAdmin pages through customers and extends a past-due Fawry subscription", async ({ page }) => {
  await openAsSuper(page);
  const ali = page.getByRole("row").filter({ hasText: "Ali" });
  await expect(ali).toContainText("past_due");
  await expect(ali).toContainText("EGP 250.00");
  await expect(page.getByRole("row").filter({ hasText: "Nile Labs" })).toHaveCount(0);

  await page.getByRole("button", { name: "Load more" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Nile Labs" })).toBeVisible();
  await expect(ali).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);

  await extend(page, "Ali", "5");
  await expect(page.getByLabel("Type extend")).toHaveCount(0);
  await expect(ali.getByText("grace", { exact: true })).toBeVisible();
  await expect(ali).toContainText("2026-11-23");
});

test("store and active subscriptions offer no grace action", async ({ page }) => {
  await openAsSuper(page);
  await expect(page.getByRole("row").filter({ hasText: "Mona" }).getByRole("button")).toHaveCount(0);
  await page.getByRole("button", { name: "Load more" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Nile Labs" }).getByRole("button")).toHaveCount(0);
});

test("the gateway's refusal shows in the dialog and leaves the row as it was", async ({ page }) => {
  await openAsSuper(page);
  await extend(page, "Refunded Co", "7");
  await expect(page.getByText("this subscription's last payment was refunded")).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Refunded Co" })).toContainText("past_due");
});

test("days outside 1-30 are refused before anything is sent", async ({ page, request }) => {
  await openAsSuper(page);
  const before = (await calls(request)).grace_calls;
  await extend(page, "Refunded Co", "31");
  const range = page.getByText("Grace must be a whole number of days from 1 to 30.");
  await expect(range).toBeVisible();
  // Reopened, the dialog starts clean, so the second refusal is this attempt's own.
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(range).toHaveCount(0);
  await extend(page, "Refunded Co", "2.5");
  await expect(range).toBeVisible();
  expect((await calls(request)).grace_calls).toBe(before);
});

test("an Admin sees why the page is empty and never asks for customers", async ({ page, request }) => {
  const before = (await calls(request)).calls;
  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await page.goto("/customers");
  await expect(page.getByText("Only a SuperAdmin can see customers and their billing.")).toBeVisible();
  // Let any request the page might wrongly start arrive before counting.
  await page.waitForLoadState("networkidle");
  expect((await calls(request)).calls).toBe(before);
});
