import http from "node:http";
import type { AddressInfo } from "node:net";

import { expect, test } from "@playwright/test";

import {
  REUSE_WINDOW_MS,
  type RefreshOutcome,
  refreshAtGateway,
  singleFlightRefresh,
} from "../../src/lib/refresh";

// Unit-level: no browser, no server -- Playwright's runner is the only one this package has.
// singleFlightRefresh keeps module-level state, so each test uses its own token names.

const REJECTED: RefreshOutcome = { ok: false, reason: "rejected" };
const UNAVAILABLE: RefreshOutcome = { ok: false, reason: "unavailable" };

function singleUseGateway(prefix: string) {
  const live = new Set([`${prefix}-0`]);
  let minted = 0;
  const calls: string[] = [];
  const run = async (token: string): Promise<RefreshOutcome> => {
    calls.push(token);
    await new Promise((resolve) => setTimeout(resolve, 10));
    if (!live.delete(token)) {
      return REJECTED;
    }
    minted += 1;
    live.add(`${prefix}-${minted}`);
    return {
      ok: true,
      tokens: { access_token: `at-${minted}`, refresh_token: `${prefix}-${minted}`, expires_in: 900 },
    };
  };
  return { run, calls };
}

function accessOf(outcome: RefreshOutcome): string | null {
  return outcome.ok ? outcome.tokens.access_token : null;
}

test("concurrent callers with one refresh token share a single refresh", async () => {
  const { run, calls } = singleUseGateway("concurrent");
  const results = await Promise.all(
    [1, 2, 3].map(() => singleFlightRefresh("concurrent-0", run)),
  );
  expect(calls).toEqual(["concurrent-0"]);
  expect(results.map(accessOf)).toEqual(["at-1", "at-1", "at-1"]);
});

test("a straggler with the spent token inside the reuse window gets the same pair", async () => {
  let clock = Date.now();
  const now = () => clock;
  const { run, calls } = singleUseGateway("straggler");
  const first = await singleFlightRefresh("straggler-0", run, now);
  expect(accessOf(first)).toBe("at-1");

  clock += REUSE_WINDOW_MS - 1;
  expect(await singleFlightRefresh("straggler-0", run, now)).toEqual(first);
  expect(calls).toHaveLength(1);

  // Past the window the spent token goes to the gateway again, which refuses it.
  clock += 2;
  expect(await singleFlightRefresh("straggler-0", run, now)).toEqual(REJECTED);
  expect(calls).toHaveLength(2);
});

test("different refresh tokens never share a result, and a failure is not cached", async () => {
  const seen: string[] = [];
  const run = async (token: string): Promise<RefreshOutcome> => {
    seen.push(token);
    return token === "good"
      ? { ok: true, tokens: { access_token: "a", refresh_token: "r", expires_in: 900 } }
      : REJECTED;
  };
  const [bad, good] = await Promise.all([
    singleFlightRefresh("bad", run),
    singleFlightRefresh("good", run),
  ]);
  expect(bad).toEqual(REJECTED);
  expect(accessOf(good)).toBe("a");
  await singleFlightRefresh("bad", run);
  expect(seen).toEqual(["bad", "good", "bad"]);
});

// INT-104: an outage is not a verdict on the token. It is never cached, so the next caller asks
// the gateway again, and a refresh that throws is reported as an outage, not a rejection.
test("an outage is reported as unavailable, is not cached, and a throwing refresh counts as one", async () => {
  let calls = 0;
  const flaky = async (): Promise<RefreshOutcome> => {
    calls += 1;
    if (calls === 1) {
      throw new TypeError("fetch failed");
    }
    if (calls === 2) {
      return UNAVAILABLE;
    }
    return { ok: true, tokens: { access_token: "after-outage", refresh_token: "r2", expires_in: 900 } };
  };
  expect(await singleFlightRefresh("flaky-0", flaky)).toEqual(UNAVAILABLE);
  expect(await singleFlightRefresh("flaky-0", flaky)).toEqual(UNAVAILABLE);
  expect(accessOf(await singleFlightRefresh("flaky-0", flaky))).toBe("after-outage");
  expect(calls).toBe(3);
});

// refreshAtGateway against a real HTTP server standing in for /api/v3/auth/refresh: only the
// gateway refusing the credential is a rejection. A 5xx, a rate limit, an unreachable gateway or
// an unreadable answer is an outage, which must not sign the operator out (INT-104).
test.describe("refreshAtGateway classifies the gateway's answer", () => {
  let server: http.Server;
  let origin: string;
  let reply: { status: number; body: string } = { status: 200, body: "{}" };
  const previous = process.env.AURA_API_URL;

  test.beforeAll(async () => {
    server = http.createServer((request, response) => {
      request.resume();
      request.on("end", () => {
        response.writeHead(reply.status, { "content-type": "application/json" });
        response.end(reply.body);
      });
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    process.env.AURA_API_URL = origin;
  });

  test.afterAll(async () => {
    process.env.AURA_API_URL = previous;
    await new Promise((resolve) => server.close(resolve));
  });

  const pair = { access_token: "a", refresh_token: "r", expires_in: 900 };
  const cases: Array<[string, number, string, RefreshOutcome]> = [
    ["200 with a pair", 200, JSON.stringify(pair), { ok: true, tokens: pair }],
    ["401", 401, '{"detail":"Refresh token is invalid, expired, or already used."}', REJECTED],
    ["403", 403, '{"detail":"forbidden"}', REJECTED],
    ["422", 422, '{"detail":[]}', REJECTED],
    ["500", 500, '{"detail":"boom"}', UNAVAILABLE],
    ["502", 502, "<html>bad gateway</html>", UNAVAILABLE],
    ["503", 503, '{"detail":"unavailable"}', UNAVAILABLE],
    // The gateway's own answer when it cannot read its auth state: retryable, not a verdict.
    ["503 auth_state_unavailable", 503, '{"error":"auth_state_unavailable","retryable":true}', UNAVAILABLE],
    ["429", 429, '{"detail":"slow down"}', UNAVAILABLE],
    ["200 that is not a token pair", 200, "<html>captive portal</html>", UNAVAILABLE],
  ];
  for (const [name, status, body, expected] of cases) {
    test(`${name} is ${expected.ok ? "rotated" : expected.reason}`, async () => {
      reply = { status, body };
      expect(await refreshAtGateway("token")).toEqual(expected);
    });
  }

  test("an unreachable gateway is unavailable, not a thrown error", async () => {
    const closed = http.createServer();
    await new Promise<void>((resolve) => closed.listen(0, "127.0.0.1", resolve));
    const port = (closed.address() as AddressInfo).port;
    await new Promise((resolve) => closed.close(resolve));
    process.env.AURA_API_URL = `http://127.0.0.1:${port}`;
    try {
      expect(await refreshAtGateway("token")).toEqual(UNAVAILABLE);
    } finally {
      process.env.AURA_API_URL = origin;
    }
  });
});
