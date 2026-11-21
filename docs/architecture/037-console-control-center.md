# ADR-037 — The console's Super-Admin control center: infrastructure tokens held by the gateway

**Status:** Proposed (stage 1 built, not proven) · **Date:** 2026-11-21 · **Related:** ADR-002
(v3 gateway), ADR-029 (service keys, caller context), ADR-030 (hosted open-weight inference),
CB-20 (Alembic is the schema authority), CB-43/CB-44 (Railway pre-deploy, no CI runners) ·
**Gap:** GAP-067 · **Plan:** `docs/plans/console-control-center-plan.md`

## Context

Running AURA means working in five other consoles: Railway (services, variables, deploys), the
Hugging Face GPU Space, Grafana Cloud, Qdrant Cloud, and the vendor dashboards that hold the
provider keys. The owner asked for one place to run all of it: the console's Settings page,
open only to the Super Admin, with no need to sign in anywhere else.

That changes what the gateway is. Until now it held provider keys for one job, inference. To
control the platforms it must hold tokens that can redeploy production, change its variables,
rotate keys, and take or restore database snapshots. Anyone who gets those tokens, or gets the
gateway to use them, owns the deployment. So the design question is not how to call five APIs.
It is what has to be true before the gateway agrees to act.

## Decision

### Who may act

- **Only the Super Admin.** Every `/api/v3/admin/control/*` route calls
  `require_super_admin`. An Admin or Developer gets 403, and the console hides the Settings nav
  item from them and renders nothing behind it (`settings/layout.tsx`). A parametrized sweep in
  `tests/unit/test_control_center.py` covers every control route. A completeness test, built
  from the app's OpenAPI paths, fails if a new route is not in the sweep.
- **Every write needs a step-up grant.** A grant is bought with a *fresh* Google sign-in:
  1. The gateway issues a one-time challenge nonce. It is stored as a digest and consumed by
     `take_grant`, so it can be used once.
  2. The console opens Google in a popup with `prompt=login` and `max_age` (300 s). The
     redirect goes to `/step-up/callback`, which posts the ID token back to its opener on the
     same origin only.
  3. The gateway checks the token through `GoogleReverifier`. It needs one of our `aud` values,
     `email_verified`, an `auth_time` within `max_age`, our nonce, and the same email as the
     session.
  4. It returns an opaque five-minute grant, stored only as SHA-256.

  The console holds the grant in memory only (never `localStorage` or a cookie) and sends it in
  `X-Aura-Step-Up`. A stolen session cookie cannot buy one: buying one needs the person's Google
  password and second factor *now*. Sign-out calls `POST /admin/control/step-up/revoke`, so a
  grant never outlives its session.
- **Every write also needs a typed confirmation**, checked on the server (`_confirm`), so a
  script cannot skip the dialog.
- **Failures are 403 with `details.reason`** (`step_up_required`, `step_up_refused`,
  `confirmation_required`), never 401. The console's proxy treats 401 as an expired session and
  redirects to the login page, which would throw away a half-typed form.
- **Writes are capped** at `AURA_CONTROL_WRITES_PER_HOUR` (default 30), counted from
  the shared journal, so the cap holds across every gateway process.

### What the gateway holds

- **Platform tokens are entered in the console and encrypted in Postgres** with the existing
  `EncryptedCredentialVault` (`AURA_SECRETS_ENCRYPTION_KEY`). Without that key, or with a malformed one, nothing
  can be connected and the page says which variable to set. The vault is built on every boot,
  so a bad key disables the control center and logs an error; it never stops the gateway.
- **Tokens are write-only.** No response, audit row, journal entry or log line contains one. The
  canary sweep in the tests checks every response and every audit event. Replacing a token
  deletes the old vault entry.
- **A token is verified before it is saved**, where the platform allows it:
  - HF: `whoami` plus the named Space.
  - Cloudflare: `/user/tokens/verify`.
  - Qdrant: `GET /collections`. The host must end in `.qdrant.io` or be `AURA_QDRANT_URL`'s
    host, so a token cannot be sent anywhere else (SSRF).
  - Railway and Grafana are saved "not verified yet" until stage 2 and stage 5 add their
    clients.

  The Cloudflare API root comes from the configured Workers AI provider's `base_url`, never a
  literal, so ADR-030's rule that hosted-inference hosts never appear in Python source still
  holds.
- **Protected variables cannot be changed from the console**: `AURA_SECRETS_ENCRYPTION_KEY`,
  `AURA_DATA_ENCRYPTION_KEYS`, `AURA_DATABASE_URL`. Changing any of them from inside the
  process that depends on it can lock the platform out of its own data. The catalog marks them,
  and the write path (stage 2) refuses them.

### How a change is made safe

- **Every change is journaled** in `aura_control_changes`: who, what, target, status, the
  deployment it produced and the one before it. A `restore_ref` points into the vault, because
  the values a rollback restores may themselves be secrets.
- **A change that leaves production unhealthy is rolled back automatically.** The `Reconciler`
  watches open (`WATCHING`) changes. If the deployment fails, or is still not healthy after
  `AURA_CONTROL_ROLLBACK_WATCH_S` (300 s), it redeploys the previous deployment and restores the
  variables. If the rollback itself fails, the change is marked `FAILED` for a human to handle.
  A Postgres lease row (`aura_control_leases`) makes sure exactly one gateway process acts, even
  with several uvicorn workers.
- **"Deploy" means redeploy, restart, rollback, status and logs.** It never means "deploy any
  branch". Shipping code stays `main` → Railway, so the console cannot ship code that has not
  been merged.

### The break-glass path

The Railway dashboard. If the gateway is down, the control center is down with it, and that is
accepted: the platform dashboards still work, and nothing in this design takes access away from
them. The control center adds a way in; it removes none.

## Consequences

- **The gateway becomes a high-value target.** A gateway compromise now reaches the platforms as
  well as the data. The step-up, the protected variables, the write cap and the journal limit
  what a stolen *session* can do. They do not limit a compromised *process*. That risk is
  accepted for the convenience asked for, and it is the reason this is a Super-Admin-only,
  step-up-gated surface rather than an Admin one.
- **Google is now part of every write.** If Google sign-in is unavailable, no change can be made
  from the console. The platform dashboards remain the way in.
- **One manual setup step:** `https://console.aureon-ai.tech/step-up/callback` must be an
  authorized redirect URI on the console's Google OAuth client. Until it is, the popup fails and
  no write can be made.
- **Schema:** migration `0031_control_center` adds four tables (`aura_control_platforms`,
  `aura_control_changes`, `aura_control_step_up`, `aura_control_leases`) with a working
  `downgrade()`. The migration round trip on PG16 covers it, and so does
  `tests/load/test_control_store_postgres.py`.

## Stages

| Stage | Scope | Built |
|---|---|---|
| S1 | Foundation: migration 0031; vault-backed tokens; step-up; journal, reconciler and audit; Settings with Overview, AURA settings (read-only) and Changes | yes (2026-11-21), not proven |
| S2 | Railway: services, variables (edit with validation), redeploy, restart, rollback, logs; the reconciler turned on | no |
| S3 | HF GPU Space: status, restart, hardware, variables | no |
| S4 | Provider keys: create and revoke where the vendor allows (Cloudflare), replace-only (Groq), HF rotated on both sides | no |
| S5 | Grafana Cloud (set up, status) and Qdrant Cloud (status, snapshots; restore needs step-up) | no |

## Evidence still owed

Built, not proven until `docs/reports/control-center-*.md` is committed. It must show, on the
live console:

1. The Super Admin opens Settings, and an Admin is refused, both in the UI and at the API.
2. A real Google step-up round trip.
3. A token saved and verified, with no response containing it.
4. From stage 2 on: one change rolled back automatically after a forced unhealthy deploy.
