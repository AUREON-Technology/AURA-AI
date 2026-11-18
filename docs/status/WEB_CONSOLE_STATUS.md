# Web console status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers `aura_admin/`, the Next.js operator console
(Next 15.5.27). On Railway it moves to its own host, `https://console.aureon-ai.tech/`, served
from the root; the VPS stacks keep `/console` (see "Domain move" below). The Flutter client's web
target (`aura_client/web/`) is a different thing: it is built locally but deployed nowhere.
Evidence scale and refresh steps: [README.md](README.md).

## Domain move to `console.aureon-ai.tech` (2026-11-18)

Plan and user decisions: [`docs/plans/console-subdomain-plan.md`](../plans/console-subdomain-plan.md).

| Step | Status | Evidence |
| --- | --- | --- |
| Image defaults to the root base path; `/console`, `/console/*` → 308 to the unprefixed path when the base path is empty | TESTED locally (2026-11-18), not CI-run | `aura_admin/Dockerfile`, `next.config.ts`; `tests/e2e/legacy-path.spec.ts`; full suite 33 passed; `tsc`, lint, and both builds (root, and `/console` showing `basePath: '/console'` with no redirects) clean |
| VPS prod/dev stacks unchanged at `/console` | BUILT (no change) | Both compose files pass `/console` as build args and runtime env |
| Railway custom domain `console.aureon-ai.tech` on `AURA-Admin` (port 3000) | LIVE 2026-11-18 | Custom domain `a9c3ee6b-…` verified, Let's Encrypt cert valid to 2027-02-15; Hostinger `CNAME console → mdgyf063.up.railway.app` and `TXT _railway-verify.console`. Before the switch, `https://console.aureon-ai.tech/console/login` → 200 and `/login` → 404 |
| Railway variables `NEXT_BASE_PATH` / `NEXT_PUBLIC_BASE_PATH` (`/console`) removed | OPEN | Do this only after the PR is merged; it is the step that moves the console to the root. Rollback: set both back to `/console` |
| Google OAuth Authorized JavaScript origin `https://console.aureon-ai.tech` | OPEN (owner) | Without it the Google sign-in button does not load on the new host |
| Live: `/login` 200, `/console/login` 308 → `/login`, operator sign-in | OPEN | Not proven until checked on the live host |

**Re-read against commit:** `b2e14c4` (2026-11-08) for every row dated 2026-11-07 or later (their live checks came first; the re-read confirmed that tree holds what they describe); older rows carry the commit they were checked at. Refresh this line whenever a row is re-verified (audit FSA-120).

## Summary

The console is built: 10 pages, Google sign-in for Developer+ roles, and a same-origin proxy that
turns a cookie into a Bearer token against `/api/v3/admin/*`. It is wired into both compose files
and both nginx server blocks. None of it is proven:
- It is **not served anywhere reachable today**: `aureon-ai.tech/console/login` returns 404 on
  Railway, the VPS takes no deploys, and the dev domain no longer resolves. *Update
  2026-11-18:* the Railway `AURA-Admin` service serves it on its generated host
  (`aura-admin-production-03530311.up.railway.app/console/login` → 200), and it is moving to
  `console.aureon-ai.tech` (table above).
- Admin CI has **never run**.
- The Track AP Super-Admin screens (AP9) have not been started.

## Capabilities

| Area / page | Status | Evidence |
| --- | --- | --- |
| Login (Google ID token → `POST /api/v3/auth/web/token`, Developer+, httpOnly SameSite=strict cookies) | TESTED (backend side only) | `app/api/auth/login/route.ts`; `tests/unit/test_web_token.py`, `test_console_operator_bootstrap.py` |
| `/api/v3/*` proxy and refresh rotation | BUILT; F-35 fix TESTED locally (2026-10-30), not CI-run | Single-flight refresh keyed by refresh token (`src/lib/refresh.ts`); `tests/e2e/refresh.spec.ts` against a mock gateway that enforces single-use refresh tokens fails without it (`[200, 401, 401]`); `tests/unit/refresh.spec.ts`. Dedupes within one server process only; the state lives on `globalThis` so the proxy and `middleware.ts` share it |
| Session gate (layout, plus nginx `auth_request` for Grafana) | BUILT; refresh-on-render TESTED locally (2026-10-30), not CI-run | `middleware.ts` (Node.js runtime) refreshes before a console page renders when the access cookie is missing or its JWT `exp` is within 30 s, through the same single-flight refresh as the proxy, sets the rotated cookies and forwards them to `app/(console)/layout.tsx` on the same request; a failed refresh clears both cookies and redirects to `/login`. `tests/e2e/session-gate.spec.ts` (4 tests: reload and sidebar navigation after expiry, an expired-`exp` cookie, an invalid refresh token, and a page render plus a proxied request sharing one refresh) — the first three fail without the middleware, the fourth fails if the refresh state is module-local. The sharing was also checked against `next build` + `next start`: 0 replays |
| Overview / Settings | BUILT; heading asserted in e2e | `tests/e2e/admin.spec.ts` |
| Providers (breaker state, circuit reset) | BUILT; e2e spec, never run in CI | `providers/page.tsx` |
| Providers: ADR-030 ceiling, `third_party`, `may_train_on_prompts` | OPEN | `router.stats()` emits them, but `src/lib/types.ts` `ProviderRow` lacks the fields |
| Prompts (pin version) | BUILT; e2e spec | `prompts/page.tsx` |
| Approvals (any kind, including `blueprint_deployment`) | BUILT; e2e spec | `approvals/page.tsx`; `admin.py` |
| Access (operators, roles, session revoke, GDPR forget, last-SuperAdmin guard) | BUILT; e2e spec; backend TESTED at `dc87beb` | `access/page.tsx`; `tests/unit/test_admin_platform.py`. #188 (`3e4e0f5`, `0e27c51`): grant/remove roles, shows who granted each, and assignments now take effect in role resolution — needs migration `0015`, which production lacks (CB-43); not CI-run (CB-44) |
| Knowledge, Models, Learning, Audit | BUILT; no spec | `knowledge/`, `models/`, `learning/`, `audit/` pages |
| Track AP Project Factory (connectors, twin, blueprint evaluate / approve / deploy / rollback) | OPEN (not started) | AP9 / GAP-058 / CB-39. Backend routes exist (`admin_adaptation.py`) with no console caller. |
| Service keys, scorecard, CAG pin | OPEN | Backend routes exist with no console caller |

## Deployment and verification

- **Production domain (Railway):** serves the backend only. `/console/login` returns 404 and
  `docs/deployment/` never mentions the console.
- **VPS (prod stack):** the `console` service is defined (`docker-compose.prod.yml`, Grafana embed
  on), but no deploy has reached the VPS in weeks, so what runs there is unknown. The last live
  test (2026-09-27, `AURA_REAL_PRODUCTION_TEST_REPORT.md` §28) found `/console` unreachable.
- **Dev stack:** `aura-dev-console` is defined (`docker-compose.dev.yml`). `dev.aureon-ai.tech` no
  longer resolves in DNS. The VPS Dev Ops workflow (`console_only` option) has never run.
- **Admin CI** (`admin-ci.yml`): has never run. **Rewired 2026-10-30 (F-37), not CI-run (CB-44).**
  - **Triggers:** `pull_request` and `push` to `main` on `aura_admin/**`, plus `workflow_dispatch`
    with an optional `base_url`. The dead `workflow_run`-of-VPS-Dev-Ops path (dev domain) is gone.
  - **Steps:** `npm ci`, `tsc --noEmit`, `npm run build`, `playwright install --with-deps
    chromium`, `npm run test:e2e` against the mock gateway the config starts itself.
  - **Lint:** deliberately not a step. There is no ESLint config, so `next lint` stops at its setup
    prompt; it gets wired in once a config is committed.
- **Playwright:** 4 e2e specs (`admin.spec.ts`, `superadmin.spec.ts`, `refresh.spec.ts`,
  `session-gate.spec.ts`) against `tests/e2e/mock-gateway.mjs`, plus `tests/unit/refresh.spec.ts`.
  They cover about 6 of 10 pages. Run locally on 2026-10-30 (`CI=1 npx playwright test`): 10
  passed, 0 failed. The `admin.spec.ts` heading locator now asks for `level: 1` (it matched both
  an `h1` and an `h2`). Ports are overridable (`MOCK_GATEWAY_PORT`, `PLAYWRIGHT_APP_PORT`) so two
  checkouts can run the suite on one host.

## Open issues (most severe first)

1. **Not served anywhere reachable.** Railway has no console, the VPS is unreachable for deploys,
   and the dev domain is gone. The owner should decide where the console lives now that the domain
   is on Railway.
2. **AP9 / GAP-058 / CB-39: no Project Factory UI.** This is the critical-path blocker for Track AP
   MVP-1; every adaptation route is reachable only through the raw API.
3. **GAP-007 / CB-30 / F-37: Admin CI has never run.** It now triggers on PRs and runs e2e against
   the mock gateway (2026-10-30), but no runner has executed it (CB-44). Locally the suite is green.
   Lint is still absent (no ESLint config).
4. **F-38 / SEC-08:** role grants from the email lists never check `email_verified`, and console
   login trusts those lists. This becomes Critical if Firebase email/password sign-in is enabled.
5. **Session refresh is tested only locally.** F-35 (the proxy) and the session gate's
   refresh-on-render (`middleware.ts`) both pass against the mock gateway, but neither has run in
   CI or against the real gateway. An access cookie that is present, not a JWT, and rejected by the
   gateway still goes to `/login` (the middleware leaves non-JWT tokens to the gateway).
6. **ADR-030 §8:** provider data ceiling and training consent are not displayed.
7. **Others.**
   - A postcss advisory whose fix needs Next 16.
   - SEC-14: the secret-scan test does not cover `aura_admin/`.
   - CLAUDE.md still calls the console "lightly used".

## Changed since 2026-10-01

- `1732618` (2026-10-11): `npm audit fix`, Next 15.5.23 → 15.5.27. Self-approvals are now
  recorded in the audit log.
- `feb32d6` (2026-10-12): the Dockerfile installs with `npm ci` from the lockfile.

## Next steps

1. Decide where the console is served (Railway service, the VPS once deploys work, or both), and
   confirm `/console/login` returns 200 there.
2. **Finish Admin CI.** PR/push triggers and mock-gateway e2e are in (2026-10-30). Still to do:
   - fix the `admin.spec.ts` heading locator so the gate can go green;
   - add an ESLint config, then a lint step;
   - link one green run (GAP-007, the M0 exit).
3. Do the Firebase email/password check, then add the `email_verified` and `sign_in_provider`
   gate (F-38).
4. Build AP9 `/console/adaptation`:
   - connectors, twin, and blueprint evaluate / approve / deploy / rollback;
   - typed-confirm dialogs;
   - mock-gateway specs;
   - a live pilot report.
6. Show the ADR-030 provider fields. Write specs for the remaining pages (M14).

## Unverified

- Whether any console container is running on the VPS.
- Whether `npm run build` and the Playwright specs pass in CI (only run locally, 2026-10-30:
  build and `tsc --noEmit` clean; Playwright as above).
- Whether the rewired Admin CI behaves on a real runner as it does locally (CB-44: no runners).
- The Firebase sign-in providers (SEC-08).
