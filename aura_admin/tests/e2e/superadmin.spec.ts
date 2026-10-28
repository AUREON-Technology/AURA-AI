import { expect, test, type Page } from "@playwright/test";

async function confirm(page: Page, text: string) {
  await page.getByLabel(`Type ${text}`).fill(text);
  await page.getByRole("button", { name: "Confirm" }).click();
}

test("superadmin can grant and remove a role, forget returns counts, last SuperAdmin demote is refused", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "SuperAdmin mock login" }).click();
  await expect(page.getByText("SuperAdmin").first()).toBeVisible();

  await page.getByRole("link", { name: "Access" }).click();
  const ops = page.getByRole("row").filter({ hasText: "ops@aura.ai" });
  const root = page.getByRole("row").filter({ hasText: "root@aura.ai" });

  await ops.getByRole("button", { name: "Make Admin" }).click();
  await confirm(page, "Admin");
  await expect(ops.getByRole("cell", { name: "Admin", exact: true })).toBeVisible();
  await expect(ops.getByRole("cell", { name: "Access page" })).toBeVisible();

  await ops.getByRole("button", { name: "Remove access" }).click();
  await confirm(page, "User");
  await expect(ops.getByRole("cell", { name: "User", exact: true })).toBeVisible();

  await page.getByPlaceholder("email or auth subject").fill("user@example.com");
  await page.getByRole("button", { name: "Forget" }).click();
  await confirm(page, "forget");
  await expect(page.getByTestId("forget-counts")).toContainText("sessions");

  await root.getByRole("button", { name: "Make Admin" }).click();
  await confirm(page, "Admin");
  await expect(page.getByText("cannot demote the last SuperAdmin")).toBeVisible();
});
