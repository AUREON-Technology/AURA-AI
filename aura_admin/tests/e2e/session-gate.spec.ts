import { expect, test, type Page } from "@playwright/test";

const gateway = process.env.AURA_API_URL || "http://127.0.0.1:8010";
const app = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";

// The session gate (middleware.ts): app/(console)/layout.tsx reads only the access cookie, so
// without the middleware a page render after the access token expired went to /login even with
// a valid refresh cookie. The mock gateway's refresh tokens are single use, like the real one's.

async function cookie(page: Page, name: string) {
  return (await page.context().cookies()).find((c) => c.name === name)?.value;
}

async function replays(request: Page["request"]): Promise<number> {
  return (await (await request.get(`${gateway}/__test/refresh-replays`)).json()).replays;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
}

// The access cookie's maxAge equals the token's lifetime, so in a browser an expired access
// token is a missing cookie. Expire it at the gateway and drop it from the browser, as the
// cookie lapsing would.
async function expireAccess(page: Page) {
  const access = await cookie(page, "aura_access");
  expect(access).toBeTruthy();
  await page.request.post(`${gateway}/__test/expire-access`, { data: { access_token: access } });
  await page.context().clearCookies({ name: "aura_access" });
  return access;
}

function jwtWithExp(exp: number) {
  const part = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${part({ alg: "HS256", typ: "JWT" })}.${part({ sub: "a1", exp })}.c2ln`;
}

test("an expired access token is refreshed on a full reload and on sidebar navigation", async ({ page }) => {
  await login(page);
  const before = await replays(page.request);

  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();

  // Full reload of a console page after expiry: a server render of the layout.
  const firstRefresh = await cookie(page, "aura_refresh");
  await expireAccess(page);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/approvals");
  expect(await cookie(page, "aura_access")).toBeTruthy();
  const secondRefresh = await cookie(page, "aura_refresh");
  expect(secondRefresh).not.toBe(firstRefresh);

  // Sidebar navigation after expiry, then a full reload of where it landed.
  await expireAccess(page);
  await page.getByRole("link", { name: "Providers" }).click();
  await expect(page.getByRole("heading", { name: "Providers", level: 1 })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Providers", level: 1 })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/providers");
  expect(await cookie(page, "aura_refresh")).not.toBe(secondRefresh);

  expect((await replays(page.request)) - before).toBe(0);
});

test("an access cookie whose JWT exp has passed is refreshed before the render", async ({ page }) => {
  await login(page);
  const before = await replays(page.request);
  const refresh = await cookie(page, "aura_refresh");

  // Still present in the browser but past its exp (e.g. a clock-skewed cookie): the gateway
  // would refuse it, so the middleware must refresh rather than pass it through.
  await page.context().addCookies([
    { name: "aura_access", value: jwtWithExp(Math.floor(Date.now() / 1000) - 60), url: app },
  ]);
  await page.goto("/prompts");
  await expect(page.getByRole("heading", { name: "Prompts", level: 1 })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/prompts");
  expect(await cookie(page, "aura_refresh")).not.toBe(refresh);
  expect((await replays(page.request)) - before).toBe(0);
});

test("an invalid refresh token sends the page render to /login and clears both cookies", async ({ page }) => {
  await login(page);
  await expireAccess(page);
  await page.context().addCookies([{ name: "aura_refresh", value: "not-a-live-token", url: app }]);

  await page.goto("/providers");
  await expect(page).toHaveURL(/\/login$/);
  expect(await cookie(page, "aura_access")).toBeFalsy();
  expect(await cookie(page, "aura_refresh")).toBeFalsy();
});

// middleware.ts and the /api/v3 proxy are bundled separately; they must still share one
// single-flight state (src/lib/refresh.ts keeps it on globalThis, and the middleware runs on
// the Node.js runtime for that reason). A page render spends the refresh token; a proxied
// request the browser had already sent with the same old cookie must reuse that result inside
// the reuse window, not replay the spent token.
test("a page render and a proxied request with the same refresh cookie share one refresh", async ({
  page,
}) => {
  await login(page);
  await expireAccess(page);
  const refresh = await cookie(page, "aura_refresh");
  const before = await replays(page.request);
  const headers = { cookie: `aura_refresh=${refresh}` };

  const render = await fetch(`${app}/providers`, { headers, redirect: "manual" });
  expect(render.status).toBe(200);
  expect(render.headers.getSetCookie().some((c) => c.startsWith("aura_access="))).toBe(true);

  const proxied = await fetch(`${app}/api/v3/admin/me`, { headers });
  expect(proxied.status).toBe(200);
  expect((await replays(page.request)) - before).toBe(0);
});

async function refreshOutage(page: Page, refreshToken: string, status: number | null) {
  await page.request.post(`${gateway}/__test/refresh-outage`, {
    data: { refresh_token: refreshToken, status },
  });
}

// INT-104: the gateway failing the refresh (5xx, unreachable) says nothing about the session.
// The render answers 503 and keeps both cookies, so the operator is still signed in once the
// gateway is back; only the gateway refusing the token (the test above) clears them.
test("a gateway outage during the refresh answers 503 and keeps the session", async ({ page }) => {
  // A session whose access token has lapsed: only the refresh cookie is left. It is minted at
  // the gateway rather than through a signed-in page, so no prefetch from an open console page
  // can spend it before the outage is in place.
  const pair = await (
    await page.request.post(`${gateway}/api/v3/auth/web/token`, { headers: { authorization: "Bearer mock_token" } })
  ).json();
  const refresh: string = pair.refresh_token;
  await page.context().addCookies([{ name: "aura_refresh", value: refresh, url: app }]);

  await refreshOutage(page, refresh, 503);
  try {
    const outage = await page.goto("/providers");
    expect(outage?.status()).toBe(503);
    expect(outage?.headers()["retry-after"]).toBeTruthy();
    await expect(page.getByRole("heading", { name: "The gateway is unavailable" })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/providers");
    expect(await cookie(page, "aura_refresh")).toBe(refresh);
  } finally {
    await refreshOutage(page, refresh, null);
  }

  await page.reload();
  await expect(page.getByRole("heading", { name: "Providers", level: 1 })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/providers");
  expect(await cookie(page, "aura_access")).toBeTruthy();
  expect(await cookie(page, "aura_refresh")).not.toBe(refresh);
});
