import { expect, test, type Page } from "@playwright/test";

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";

async function accessCookie(page: Page) {
  return (await page.context().cookies()).find((c) => c.name === "aura_access")?.value;
}

// F-35 / roadmap R4.5: the mock gateway's refresh tokens are single use, like the real one's.
// Once the access token expires, every request a page fires at once gets a 401 and refreshes
// with the same cookie. Without a single-flight refresh in the proxy only the first succeeds;
// the rest come back 401, which api.ts turns into a redirect to /login.
test("three parallel requests after the access token expired share one refresh and stay logged in", async ({
  page,
  request,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();

  const access = await accessCookie(page);
  expect(access).toBeTruthy();
  const { replays: before } = await (await request.get(`${gateway}/__test/refresh-replays`)).json();
  await request.post(`${gateway}/__test/expire-access`, { data: { access_token: access } });

  const statuses = await page.evaluate(() =>
    Promise.all(
      ["/api/v3/admin/status", "/api/v3/admin/me", "/api/v3/admin/approvals"].map((path) =>
        fetch(path, { credentials: "same-origin" }).then((r) => r.status),
      ),
    ),
  );
  expect(statuses).toEqual([200, 200, 200]);

  const { replays: after } = await (await request.get(`${gateway}/__test/refresh-replays`)).json();
  expect(after - before).toBe(0);
  expect(await accessCookie(page)).not.toBe(access);

  // The rotated cookies landed: a full reload renders the console, not the login page.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  expect(new URL(page.url()).pathname).not.toBe("/login");
});
