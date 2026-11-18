import { apiOrigin } from "./session";

export type RotatedTokens = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

// What a refresh attempt says about the session (audit INT-104). Only "rejected" -- the gateway
// refusing the refresh token -- means the session is over and its cookies should go. An outage
// ("unavailable": a 5xx, a rate limit, an unreachable gateway, an unreadable answer) says nothing
// about the token, which on the real gateway is only spent once a new pair is minted; signing
// every operator out on a gateway blip would be the console's doing, not the gateway's.
export type RefreshOutcome =
  | { ok: true; tokens: RotatedTokens }
  | { ok: false; reason: "rejected" | "unavailable" };

const UNAVAILABLE: RefreshOutcome = { ok: false, reason: "unavailable" };

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
  inflight: Map<string, Promise<RefreshOutcome>>;
  recent: Map<string, { tokens: RotatedTokens; until: number }>;
};

const STATE_KEY = Symbol.for("aura_admin.singleFlightRefresh");
const holder = globalThis as typeof globalThis & { [STATE_KEY]?: RefreshState };
const state: RefreshState = (holder[STATE_KEY] ??= { inflight: new Map(), recent: new Map() });
const { inflight, recent } = state;

// Only a rotated pair is remembered. A rejection or an outage is not: the next caller asks the
// gateway again, so a refresh that failed during an outage succeeds once the gateway is back.
// A `run` that throws is an outage, never a rejection.
export function singleFlightRefresh(
  refreshToken: string,
  run: (refreshToken: string) => Promise<RefreshOutcome>,
  now: () => number = Date.now,
): Promise<RefreshOutcome> {
  const at = now();
  for (const [key, entry] of recent) {
    if (entry.until <= at) {
      recent.delete(key);
    }
  }
  const settled = recent.get(refreshToken);
  if (settled) {
    return Promise.resolve({ ok: true, tokens: settled.tokens });
  }
  const pending = inflight.get(refreshToken);
  if (pending) {
    return pending;
  }
  const promise = Promise.resolve()
    .then(() => run(refreshToken))
    .catch((): RefreshOutcome => UNAVAILABLE)
    .then((outcome) => {
      if (outcome.ok) {
        recent.set(refreshToken, { tokens: outcome.tokens, until: now() + REUSE_WINDOW_MS });
      }
      return outcome;
    })
    .finally(() => {
      inflight.delete(refreshToken);
    });
  inflight.set(refreshToken, promise);
  return promise;
}

function isTokenPair(body: unknown): body is RotatedTokens {
  const pair = body as Partial<RotatedTokens> | null;
  return (
    typeof pair === "object" &&
    pair !== null &&
    typeof pair.access_token === "string" &&
    typeof pair.refresh_token === "string"
  );
}

// The one call to the gateway's refresh endpoint, used by the proxy, the middleware and the
// session check so they cannot drift apart. The gateway answers 401 for a refresh token that is
// invalid, expired, spent or revoked (api/v2/auth.py `refresh`); any other 4xx except a timeout
// or a rate limit is likewise a verdict on this request, so it is a rejection. Everything else
// is an outage and leaves the session alone.
export async function refreshAtGateway(refreshToken: string): Promise<RefreshOutcome> {
  let upstream: Response;
  try {
    upstream = await fetch(`${apiOrigin()}/api/v3/auth/refresh`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
  } catch {
    return UNAVAILABLE;
  }
  const { status } = upstream;
  if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
    return { ok: false, reason: "rejected" };
  }
  if (!upstream.ok) {
    return UNAVAILABLE;
  }
  const body: unknown = await upstream.json().catch(() => null);
  return isTokenPair(body) ? { ok: true, tokens: body } : UNAVAILABLE;
}
