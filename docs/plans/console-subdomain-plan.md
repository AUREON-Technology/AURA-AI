# Plan: serve the operator console at `console.aureon-ai.tech` (no `/console` prefix)

Branch `feat/console-subdomain`, cut from `origin/main@446e004b` (2026-11-18).

## Goal

The operator console (`aura_admin/`) is reached at `https://console.aureon-ai.tech/`, with its
pages at the root (`/login`, `/providers`, ...) instead of under `/console`.

## User decisions (do not re-ask) — 2026-11-18

1. The console is served at the root of `console.aureon-ai.tech`. Its base path is empty.
2. Old `/console/...` URLs on the console host get a permanent redirect to the same path
   without the prefix (`/console/providers` → `/providers`). This keeps bookmarks and the
   generated `*.up.railway.app/console` links working.
3. The VPS prod and dev stacks keep serving the console under `/console` behind their own nginx.
   Their compose files already pass `/console` explicitly; nothing there changes.
4. The `aura_admin` image defaults to the root base path. A deployment that wants a prefix (the
   VPS compose files) passes it as a build arg.
5. Claude performs the infrastructure steps: adds the Railway custom domain, publishes the
   Hostinger DNS records, removes the Railway path variables, and verifies the result. The owner
   adds the Google OAuth JavaScript origin, because Claude cannot reach the Google Cloud console.

## Current state (read 2026-11-18)

- Railway project `intuitive-adaptation`, service `AURA-Admin`:
  - builds `aura_admin/` from `main` (last deploy `446e004b`);
  - has variables `NEXT_BASE_PATH=/console` and `NEXT_PUBLIC_BASE_PATH=/console`;
  - has no custom domain. Only `aura-admin-production-03530311.up.railway.app/console/login`
    answers 200.
- `console.aureon-ai.tech` does not resolve.
- In code, every path goes through those two variables (`next.config.ts` `basePath`,
  `src/lib/api.ts`, `app/login/page.tsx`, `src/components/shell.tsx`). Session cookies are
  host-only with `path: "/"`, and the CSRF guard (`src/lib/same-origin.ts`) compares Origin with
  the request's own host. Neither needs to change for a new host.
- The backend has no dependency on the console's URL. The console calls it server-side through
  `AURA_API_URL`, so no CORS rule is involved.

## Web console (`aura_admin/`)

- `Dockerfile`: the `NEXT_BASE_PATH` and `NEXT_PUBLIC_BASE_PATH` build-arg defaults change from
  `/console` to empty. The runner stage's `ENV` lines follow the build args instead of hard-coding
  `/console` (they are informational at runtime; the value is baked at build).
- `next.config.ts`: add a `redirects()` that applies only when the base path is empty:
  `/console` → `/` and `/console/:path*` → `/:path*`, both permanent (308). Next evaluates
  redirects before `middleware.ts`, so the session gate never sees a `/console` path.
- Tests: a Playwright spec, `tests/e2e/legacy-path.spec.ts`, asserts that `/console/login`
  lands on `/login` and that `/console` lands on the overview (or on `/login` when signed out).
  The suite already runs the app at the root path, so the existing specs cover the new layout.

## Deploy and config

- `deploy/docker-compose.prod.yml` and `docker-compose.dev.yml`: unchanged. They pass `/console`
  as build args and runtime env.
- nginx template and `host-proxy.conf.example`: unchanged (VPS keeps `/console`).
- Railway `AURA-Admin`:
  1. Add the custom domain `console.aureon-ai.tech` (target port 3000).
  2. Publish at Hostinger: `CNAME console` → the Railway target the API returns, and
     `TXT _railway-verify.console` → the token from `status.verificationDnsHost/Token`.
  3. Wait for verification and the certificate.
  4. After the PR is merged, delete `NEXT_BASE_PATH` and `NEXT_PUBLIC_BASE_PATH`, which
     redeploys the service at the root path.
- Google Cloud (owner): add `https://console.aureon-ai.tech` to the OAuth client's Authorized
  JavaScript origins. Without it the Google sign-in button refuses to load on the new host.

## Contract

No API, field, event or enum changes. Only the console's own URL layout changes, and old URLs
redirect.

## Rollout order

1. Google origin added by the owner (can happen any time before step 4).
2. Custom domain and DNS (the console keeps serving `/console` on the new host meanwhile).
3. Merge the PR. Railway's variables still say `/console`, so behaviour does not change yet.
4. Delete the two Railway variables. Railway rebuilds with the new image default, which is the
   root path.

Rollback: set the two Railway variables back to `/console`; the redirect rule switches itself off.

## Verification

Automated (in `aura_admin/`):
- `npx tsc --noEmit`
- `npm run lint`
- `npm run build`
- `CI=1 npx playwright test`
- `NEXT_BASE_PATH=/console NEXT_PUBLIC_BASE_PATH=/console npm run build`, to show the VPS
  configuration still builds.

Live, after rollout:
- `https://console.aureon-ai.tech/login` returns 200 over a valid certificate.
- `/console/login` returns 308 to `/login`.
- `/` signed out lands on `/login`.

Manual (owner): the checklist handed over after implementation.

## Docs

`docs/status/WEB_CONSOLE_STATUS.md` (where it is served, change-log line), `CLAUDE.md` (the
`aura_admin` bullet), `docs/guides/administrator-guide.md`, `docs/guides/deployment-guide.md`
where they name the console URL.

## Out of scope

- Moving the VPS stacks to a subdomain.
- The Railway console building with Grafana embeds on while Railway runs no Grafana (noted in
  the status file as an open issue).
- Releasing the apex `aureon-ai.tech` from the old Railway project.
