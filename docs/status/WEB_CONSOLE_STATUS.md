# Web console status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers `aura_admin/`, the Next.js operator console
(Next 15.5.27) served under `/console`. The Flutter client's web target (`aura_client/web/`) is a
different thing: it is built locally but deployed nowhere. Evidence scale and refresh steps:
[README.md](README.md).

## Summary

The console is built: 10 pages, Google sign-in for Developer+ roles, and a same-origin proxy that
turns a cookie into a Bearer token against `/api/v3/admin/*`. It is wired into both compose files
and both nginx server blocks. None of it is proven:
- It is **not served anywhere reachable today**: `aureon-ai.tech/console/login` returns 404 on
  Railway, the VPS takes no deploys, and the dev domain no longer resolves.
- Admin CI has **never run**.
- The Track AP Super-Admin screens (AP9) have not been started.

## Capabilities

| Area / page | Status | Evidence |
| --- | --- | --- |
| Login (Google ID token → `POST /api/v3/auth/web/token`, Developer+, httpOnly SameSite=strict cookies) | TESTED (backend side only) | `app/api/auth/login/route.ts`; `tests/unit/test_web_token.py`, `test_console_operator_bootstrap.py` |
| `/api/v3/*` proxy and refresh rotation | BUILT; F-35 fix TESTED locally (2026-10-30), not CI-run | Single-flight refresh keyed by refresh token (`src/lib/refresh.ts`); `tests/e2e/refresh.spec.ts` against a mock gateway that enforces single-use refresh tokens fails without it (`[200, 401, 401]`); `tests/unit/refresh.spec.ts`. Dedupes within one server process only |
| Session gate (layout, plus nginx `auth_request` for Grafana) | BUILT; defect | `app/(console)/layout.tsx` → `/api/v3/admin/me`. It never refreshes: a page render after the access token expired redirects to `/login` (seen locally in dev, 2026-10-30) |
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
- **Admin CI** (`admin-ci.yml`): has never run.
  - **Triggers:** only `workflow_run` of VPS Dev Ops (never run) and `workflow_dispatch`. There is
    no PR trigger (GAP-007, F-37, CB-30).
  - **Lint:** a no-op. There is no ESLint config, so `next lint` exits at its setup prompt.
  - **e2e:** its `workflow_run` path points Playwright at the dev domain rather than the mock
    gateway the specs expect.
- **Playwright:** 3 e2e specs (`admin.spec.ts`, `superadmin.spec.ts`, `refresh.spec.ts`) against
  `tests/e2e/mock-gateway.mjs`, plus `tests/unit/refresh.spec.ts`. They cover about 5 of 10 pages.
  Run locally on 2026-10-30: 5 passed, 1 failed — `admin.spec.ts` fails on clean `main` too
  (`getByRole("heading", { name: "Overview" })` matches both an `h1` and an `h2`).

## Open issues (most severe first)

1. **Not served anywhere reachable.** Railway has no console, the VPS is unreachable for deploys,
   and the dev domain is gone. The owner should decide where the console lives now that the domain
   is on Railway.
2. **AP9 / GAP-058 / CB-39: no Project Factory UI.** This is the critical-path blocker for Track AP
   MVP-1; every adaptation route is reachable only through the raw API.
3. **GAP-007 / CB-30 / F-37: Admin CI never runs.** Lint checks nothing, and the e2e target is
   misconfigured.
4. **F-38 / SEC-08:** role grants from the email lists never check `email_verified`, and console
   login trusts those lists. This becomes Critical if Firebase email/password sign-in is enabled.
5. **Session gate never refreshes.** The console layout checks the access cookie alone, so a page
   render after its 15 minutes redirects to `/login` even with a valid refresh cookie. A fix needs
   middleware (a server component cannot set cookies). F-35 (parallel 401s spending the single-use
   refresh token) is fixed in the proxy but built and tested only locally.
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
2. **Fix Admin CI.**
   - Add `pull_request` and `push` triggers on `aura_admin/**`.
   - Run e2e against the mock gateway.
   - Add an ESLint config.
   - Gate the `workflow_run` path on success.
   - Link one green run (GAP-007, the M0 exit).
3. Do the Firebase email/password check, then add the `email_verified` and `sign_in_provider`
   gate (F-38).
4. Refresh in the session gate too (middleware), with a spec that reloads after expiry. Fix the
   `admin.spec.ts` heading locator.
5. Build AP9 `/console/adaptation`:
   - connectors, twin, and blueprint evaluate / approve / deploy / rollback;
   - typed-confirm dialogs;
   - mock-gateway specs;
   - a live pilot report.
6. Show the ADR-030 provider fields. Write specs for the remaining pages (M14).

## Unverified

- Whether any console container is running on the VPS.
- Whether `npm run build` and the Playwright specs pass in CI (only run locally, 2026-10-30:
  build and `tsc --noEmit` clean; Playwright as above).
- How Admin CI's dev-URL e2e path would actually behave (inferred from code).
- The Firebase sign-in providers (SEC-08).
