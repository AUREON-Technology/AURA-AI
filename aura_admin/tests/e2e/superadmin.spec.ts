import { expect, test } from "@playwright/test";

test("superadmin can grant a role, forget returns counts, last SuperAdmin demote is refused", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "SuperAdmin mock login" }).click();
  await expect(page.getByText("SuperAdmin").first()).toBeVisible();

  await page.getByRole("link", { name: "Access" }).click();
  await page.getByRole("button", { name: "Make Admin" }).first().click();
  await page.locator("input").last().fill("Admin");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("ops@aura.ai")).toBeVisible();

  await page.getByPlaceholder("email or auth subject").fill("user@example.com");
  await page.getByRole("button", { name: "Forget" }).click();
  await page.locator("input").last().fill("forget");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("forget-counts")).toContainText("sessions");

  await page.getByRole("button", { name: "Make Admin" }).last().click();
  await page.locator("input").last().fill("Admin");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("cannot demote the last SuperAdmin")).toBeVisible();
});
