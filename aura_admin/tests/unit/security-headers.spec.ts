import { expect, test } from "@playwright/test";

import {
  API_CONTENT_SECURITY_POLICY,
  frameAncestorsFor,
  pageContentSecurityPolicy,
  staticSecurityHeaders,
} from "../../src/lib/security-headers";

// F50: the policy the console sends itself (Railway has no nginx in front of it).

function directive(csp: string, name: string): string[] {
  const part = csp.split(";").map((p) => p.trim()).find((p) => p.split(/\s+/)[0] === name);
  return part ? part.split(/\s+/).slice(1) : [];
}

test("the page CSP trusts scripts by nonce, never by 'unsafe-inline', and only evaluates in dev", () => {
  const prod = pageContentSecurityPolicy({ nonce: "abc123", dev: false, frameAncestors: "'none'" });
  const scripts = directive(prod, "script-src");
  expect(scripts).toContain("'nonce-abc123'");
  expect(scripts).toContain("'strict-dynamic'");
  expect(scripts).not.toContain("'unsafe-inline'");
  expect(scripts).not.toContain("'unsafe-eval'");
  expect(directive(prod, "connect-src")).not.toContain("ws:");
  expect(directive(prod, "object-src")).toEqual(["'none'"]);
  expect(directive(prod, "base-uri")).toEqual(["'self'"]);
  expect(directive(prod, "frame-ancestors")).toEqual(["'none'"]);

  const dev = pageContentSecurityPolicy({ nonce: "abc123", dev: true, frameAncestors: "'none'" });
  expect(directive(dev, "script-src")).toContain("'unsafe-eval'");
});

test("framing is refused on the console's own host and same-origin only under a base path", () => {
  expect(frameAncestorsFor("")).toBe("'none'");
  expect(frameAncestorsFor("/console")).toBe("'self'");
  const header = (basePath: string) =>
    staticSecurityHeaders(basePath).find((h) => h.key === "X-Frame-Options")?.value;
  expect(header("")).toBe("DENY");
  expect(header("/console")).toBe("SAMEORIGIN");
});

test("the static floor matches nginx's values and the API policy loads nothing", () => {
  const byKey = Object.fromEntries(staticSecurityHeaders("").map((h) => [h.key, h.value]));
  expect(byKey).toEqual({
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "X-Frame-Options": "DENY",
  });
  expect(API_CONTENT_SECURITY_POLICY).toBe("default-src 'none'; frame-ancestors 'none'");
});
