import { apiOrigin } from "./session";

export type RotatedTokens = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

// The gateway's refresh tokens are single use: /api/v3/auth/refresh consumes the token with an
// atomic GETDEL and mints a new pair (aura_backend/services/jwt_auth.py, _refresh_store_take).
// A page that fires several requests after its access token expired sends the same refresh
// cookie on all of them, so without this every request but the first failed its refresh, came
// back 401, and api.ts sent the admin to /login (audit F-35).
//
// Keyed by the refresh token value, so two admins (or two tabs with different sessions) never
// share a result. A caller that arrives while a refresh is in flight joins that promise; one
// that arrives shortly after it settled -- a request the browser sent with the old cookie before
// the first response landed -- reuses the result instead of replaying a spent token. This only
// dedupes within one server process, which is how the console is deployed (one standalone
// Next.js process behind nginx).
//
// Two callers share it: the /api/v3 proxy route and middleware.ts (the session gate for page
// renders). Each is bundled separately, so module-level Maps would give each its own copy and
// let a page navigation and a proxied fetch both spend the same token. The state therefore
// lives on globalThis, which both see because middleware runs on the Node.js runtime in the
// same process (middleware.ts, `runtime: "nodejs"`); on the Edge runtime it would be a
// separate sandbox and this sharing would silently stop.
export const REUSE_WINDOW_MS = 10_000;

type RefreshState = {
  inflight: Map<string, Promise<RotatedTokens | null>>;
  recent: Map<string, { tokens: RotatedTokens; until: number }>;
};

const STATE_KEY = Symbol.for("aura_admin.singleFlightRefresh");
const holder = globalThis as typeof globalThis & { [STATE_KEY]?: RefreshState };
const state: RefreshState = (holder[STATE_KEY] ??= { inflight: new Map(), recent: new Map() });
const { inflight, recent } = state;

export function singleFlightRefresh(
  refreshToken: string,
  run: (refreshToken: string) => Promise<RotatedTokens | null>,
  now: () => number = Date.now,
): Promise<RotatedTokens | null> {
  const at = now();
  for (const [key, entry] of recent) {
    if (entry.until <= at) {
      recent.delete(key);
    }
  }
  const settled = recent.get(refreshToken);
  if (settled) {
    return Promise.resolve(settled.tokens);
  }
  const pending = inflight.get(refreshToken);
  if (pending) {
    return pending;
  }
  const promise = run(refreshToken)
    .then((tokens) => {
      if (tokens) {
        recent.set(refreshToken, { tokens, until: now() + REUSE_WINDOW_MS });
      }
      return tokens;
    })
    .finally(() => {
      inflight.delete(refreshToken);
    });
  inflight.set(refreshToken, promise);
  return promise;
}

// The one call to the gateway's refresh endpoint, used by both the proxy and the middleware so
// the two cannot drift apart.
export async function refreshAtGateway(refreshToken: string): Promise<RotatedTokens | null> {
  const upstream = await fetch(`${apiOrigin()}/api/v3/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!upstream.ok) {
    return null;
  }
  return (await upstream.json()) as RotatedTokens;
}
