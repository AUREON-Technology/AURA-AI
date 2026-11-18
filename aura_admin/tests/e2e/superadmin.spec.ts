import { expect, test, type Page } from "@playwright/test";

async function confirm(page: Page, text: string) {
  await page.getByLabel(`Type ${text}`).fill(text);
  await page.getByRole("button", { name: "Confirm" }).click();
}

test("superadmin can grant and remove a role, forget returns counts, an env-granted role cannot be lowered", async ({
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

  await ops.getByRole("button", { name: "Revoke sessions" }).click();
  await confirm(page, "revoke");
  await expect(page.getByLabel("Type revoke")).toHaveCount(0);

  await page.getByPlaceholder("email or auth subject").fill("user@example.com");
  await page.getByRole("button", { name: "Forget" }).click();
  await confirm(page, "user@example.com");
  await expect(page.getByTestId("forget-counts")).toContainText("sessions");
  await expect(page.getByTestId("forget-status")).toHaveText("Erasure complete.");

  // root@ is granted SuperAdmin by SUPERADMIN_EMAILS: the real handler checks that floor first
  // and answers 409 naming where to change it (api/admin_platform.py assign_role).
  await root.getByRole("button", { name: "Make Admin" }).click();
  await confirm(page, "Admin");
  await expect(page.getByText("is granted SuperAdmin by SUPERADMIN_EMAILS")).toBeVisible();
});

// F51: erasure is irreversible, so its confirmation is the subject being erased (as granting a
// role asks for the role), not a fixed word that confirms whichever subject happens to be typed.
test("the GDPR forget dialog asks for the subject itself, and a fixed word does not confirm it", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "SuperAdmin mock login" }).click();
  await expect(page.getByText("SuperAdmin").first()).toBeVisible();
  await page.getByRole("link", { name: "Access" }).click();

  const forget = page.getByRole("button", { name: "Forget" });
  await expect(forget).toBeDisabled();

  await page.getByPlaceholder("email or auth subject").fill("  erase-me@example.com ");
  await forget.click();
  await expect(page.getByRole("dialog")).toContainText("Forget erase-me@example.com");
  await page.getByLabel("Type erase-me@example.com").fill("forget");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Type erase-me@example.com to confirm.")).toBeVisible();
  await expect(page.getByTestId("forget-status")).toHaveCount(0);

  await confirm(page, "erase-me@example.com");
  await expect(page.getByTestId("forget-status")).toHaveText("Erasure complete.");
});
