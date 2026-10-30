import { expect, test } from "@playwright/test";

import { REUSE_WINDOW_MS, type RotatedTokens, singleFlightRefresh } from "../../src/lib/refresh";

// Unit-level: no browser, no server -- Playwright's runner is the only one this package has.
// singleFlightRefresh keeps module-level state, so each test uses its own token names.

function singleUseGateway(prefix: string) {
  const live = new Set([`${prefix}-0`]);
  let minted = 0;
  const calls: string[] = [];
  const run = async (token: string): Promise<RotatedTokens | null> => {
    calls.push(token);
    await new Promise((resolve) => setTimeout(resolve, 10));
    if (!live.delete(token)) {
      return null;
    }
    minted += 1;
    live.add(`${prefix}-${minted}`);
    return { access_token: `at-${minted}`, refresh_token: `${prefix}-${minted}`, expires_in: 900 };
  };
  return { run, calls };
}

test("concurrent callers with one refresh token share a single refresh", async () => {
  const { run, calls } = singleUseGateway("concurrent");
  const results = await Promise.all(
    [1, 2, 3].map(() => singleFlightRefresh("concurrent-0", run)),
  );
  expect(calls).toEqual(["concurrent-0"]);
  expect(results.map((r) => r?.access_token)).toEqual(["at-1", "at-1", "at-1"]);
});

test("a straggler with the spent token inside the reuse window gets the same pair", async () => {
  let clock = Date.now();
  const now = () => clock;
  const { run, calls } = singleUseGateway("straggler");
  const first = await singleFlightRefresh("straggler-0", run, now);
  expect(first?.access_token).toBe("at-1");

  clock += REUSE_WINDOW_MS - 1;
  expect(await singleFlightRefresh("straggler-0", run, now)).toEqual(first);
  expect(calls).toHaveLength(1);

  // Past the window the spent token goes to the gateway again, which refuses it.
  clock += 2;
  expect(await singleFlightRefresh("straggler-0", run, now)).toBeNull();
  expect(calls).toHaveLength(2);
});

test("different refresh tokens never share a result, and a failure is not cached", async () => {
  const seen: string[] = [];
  const run = async (token: string) => {
    seen.push(token);
    return token === "good" ? { access_token: "a", refresh_token: "r", expires_in: 900 } : null;
  };
  const [bad, good] = await Promise.all([
    singleFlightRefresh("bad", run),
    singleFlightRefresh("good", run),
  ]);
  expect(bad).toBeNull();
  expect(good?.access_token).toBe("a");
  await singleFlightRefresh("bad", run);
  expect(seen).toEqual(["bad", "good", "bad"]);
});
