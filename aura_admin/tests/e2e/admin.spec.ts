import { expect, test } from "@playwright/test";

test("admin can reset a circuit, pin a prompt, and decide an approval", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByTestId("insecure-banner")).toBeVisible();

  await page.getByRole("link", { name: "Providers" }).click();
  await page.getByRole("button", { name: "Reset circuit" }).click();
  await page.locator("input").last().fill("echo");
  await page.getByRole("button", { name: "Confirm" }).click();

  await page.getByRole("link", { name: "Prompts" }).click();
  await page.getByRole("button", { name: "Pin 1.0.0" }).click();
  await page.locator("input").last().fill("1.0.0");
  await page.getByRole("button", { name: "Confirm" }).click();

  await page.getByRole("link", { name: "Approvals" }).click();
  await page.getByRole("button", { name: "Approve" }).click();
  await page.locator("input").last().fill("approve");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("approved")).toBeVisible();
});
