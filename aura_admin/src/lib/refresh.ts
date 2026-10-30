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
export const REUSE_WINDOW_MS = 10_000;

const inflight = new Map<string, Promise<RotatedTokens | null>>();
const recent = new Map<string, { tokens: RotatedTokens; until: number }>();

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
