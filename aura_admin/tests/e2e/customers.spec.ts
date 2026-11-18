import { expect, test, type Page } from "@playwright/test";

// SaaS P1-F3: the Customers page and its one write, "Extend grace" (spec §5.4 "Owner tooling";
// aura_core/api/billing.py, both routes SuperAdmin-only). The mock pages at 3 rows, so its four
// customers take two pages.

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";

async function openAsSuper(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "SuperAdmin mock login" }).click();
  await expect(page.getByText("SuperAdmin").first()).toBeVisible();
  await page.getByRole("link", { name: "Customers" }).click();
  await expect(page.getByRole("heading", { name: "Customers", level: 1 })).toBeVisible();
}

async function extend(page: Page, workspace: string, days: string) {
  await page.getByRole("row").filter({ hasText: workspace }).getByRole("button", { name: "Extend grace" }).click();
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

test("days outside 1-30 are refused before anything is sent", async ({ page }) => {
  await openAsSuper(page);
  await extend(page, "Refunded Co", "31");
  const range = page.getByText("Grace must be a whole number of days from 1 to 30.");
  await expect(range).toBeVisible();
  await page.getByLabel("Days of grace").fill("2.5");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(range).toBeVisible();
  // Had either value been sent, the gateway's refund refusal would show instead.
  await expect(page.getByText("this subscription's last payment was refunded")).toHaveCount(0);
});

test("an Admin sees why the page is empty and never asks for customers", async ({ page, request }) => {
  const before = (await (await request.get(`${gateway}/__test/customer-lists`)).json()).calls;
  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await page.goto("/customers");
  await expect(page.getByText("Only a SuperAdmin can see customers and their billing.")).toBeVisible();
  const after = (await (await request.get(`${gateway}/__test/customer-lists`)).json()).calls;
  expect(after).toBe(before);
});
