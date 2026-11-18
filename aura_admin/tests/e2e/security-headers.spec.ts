import { expect, test } from "@playwright/test";

const app = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";

// F50: on Railway the console is its own host with no nginx in front of it, so the security
// headers nginx adds on the VPS have to come from the app itself (next.config.ts headers() and,
// for the per-request CSP nonce, middleware.ts).

function directives(csp: string): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const part of csp.split(";")) {
    const [name, ...values] = part.trim().split(/\s+/);
    if (name) {
      map.set(name, values);
    }
  }
  return map;
}

function nonceOf(csp: string): string {
  const match = /'nonce-([^']+)'/.exec(csp);
  expect(match, `no nonce in ${csp}`).toBeTruthy();
  return match![1];
}

test("a page carries the security headers and a per-request nonce CSP that its scripts use", async () => {
  const first = await fetch(`${app}/login`);
  expect(first.status).toBe(200);
  const h = first.headers;
  expect(h.get("strict-transport-security")).toBe("max-age=63072000; includeSubDomains");
  expect(h.get("x-content-type-options")).toBe("nosniff");
  expect(h.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
  expect(h.get("permissions-policy")).toBe("geolocation=(), microphone=(), camera=()");
  expect(h.get("x-frame-options")).toBe("DENY");

  const csp = h.get("content-security-policy") ?? "";
  const d = directives(csp);
  expect(d.get("default-src")).toEqual(["'self'"]);
  expect(d.get("frame-ancestors")).toEqual(["'none'"]);
  expect(d.get("object-src")).toEqual(["'none'"]);
  expect(d.get("base-uri")).toEqual(["'self'"]);
  const scriptSrc = d.get("script-src") ?? [];
  expect(scriptSrc).toContain("'strict-dynamic'");
  expect(scriptSrc).not.toContain("'unsafe-inline'");
  const nonce = nonceOf(csp);

  // Every script Next rendered carries this response's nonce; none would run without it.
  const html = await first.text();
  const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((m) => m[0]);
  expect(scripts.length).toBeGreaterThan(0);
  for (const tag of scripts) {
    expect(tag).toContain(`nonce="${nonce}"`);
  }

  const second = await fetch(`${app}/login`);
  expect(nonceOf(second.headers.get("content-security-policy") ?? "")).not.toBe(nonce);
});

test("API responses carry a locked-down CSP and the same header floor", async () => {
  const response = await fetch(`${app}/api/v3/admin/me`);
  expect(response.headers.get("content-security-policy")).toBe("default-src 'none'; frame-ancestors 'none'");
  expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  expect(response.headers.get("x-frame-options")).toBe("DENY");
  expect(response.headers.get("strict-transport-security")).toBeTruthy();
});

test("signing in and rendering the console raises no CSP violation", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/content security policy/i.test(message.text())) {
      violations.push(message.text());
    }
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      console.error(`Content Security Policy violation: ${event.violatedDirective} ${event.blockedURI}`);
    });
  });

  await page.goto("/login");
  await page.getByRole("button", { name: "Developer mock login" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "Providers" }).click();
  await expect(page.getByRole("heading", { name: "Providers", level: 1 })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Providers", level: 1 })).toBeVisible();

  expect(violations).toEqual([]);
});
