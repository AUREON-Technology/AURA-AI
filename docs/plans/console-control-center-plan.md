# Plan: Console Settings → a Super-Admin control center for the whole system

**Status:** Draft, awaiting owner approval · **Date:** 2026-11-21 · **Base:** `main@032e47f`
· **Branch:** `anas/laughing-hamilton-glfevj` (restarted from `main`)

## Goal

Turn the console's Settings page (`console.aureon-ai.tech/settings`) into the one place where the
Super Admin runs the system: servers, services, deploys, variables and keys on Railway, the
Hugging Face GPU Space, Grafana Cloud and Qdrant Cloud, without opening any of those platforms'
dashboards after a one-time token setup. Only the Super Admin can see the page or change anything
on it.

## Today

- `aura_admin/app/(console)/settings/page.tsx` (32 lines) prints `/admin/status → settings` as
  read-only JSON. Every Admin can see it.
- Settings are frozen dataclasses read from `AURA_*` environment variables at boot
  (`aura_core/config/settings.py`). A change means editing the platform variable and redeploying.
- No code calls Railway, Hugging Face, Grafana or Qdrant management APIs. Only CI holds
  `RAILWAY_TOKEN` (`control-plane-deploy.yml`) and `HF_TOKEN` (`inference-plane-deploy.yml`).
- Reusable pieces: `require_super_admin` and `audit_admin` (`aura_core/api/v3.py:551`), the
  console's `me.is_super_admin` gating and typed-confirm dialog, `EncryptedCredentialVault`
  (`adapters/credential_vault.py`, `AURA_SECRETS_ENCRYPTION_KEY`, never plaintext), and
  `huggingface-hub` / `httpx` (both already locked).

## User decisions (do not re-ask)

Recorded 2026-11-21.

1. **Only the Super Admin can open Settings, to view or to edit.** Admins and Developers no
   longer see the page or its nav item. The API refuses them with 403.
2. **Platforms in scope:**
   - Railway services: gateway, console, Postgres, Redis and the backup cron.
   - The Hugging Face GPU Space.
   - The AURA settings themselves.
   - Grafana Cloud, Qdrant Cloud and the provider API keys.
3. **Every write needs a fresh re-sign-in and a typed confirmation.** Viewing needs a Super Admin
   session. A write also needs a Google re-authentication from the last 5 minutes, plus typing the
   name of the thing being changed.
4. **Deploy means redeploy, restart, roll back to any earlier successful deployment, and
   deployment status and logs.** Code still reaches production only by merging to `main`. There is
   no "deploy any branch".
5. **Secrets are write-only.** The page shows name, set/not set and when last changed, and allows
   replace or delete. A secret value is never sent back to the browser.
6. **The control code runs in the gateway, with safeguards.** One SuperAdmin-only API under
   `/api/v3/admin/control/*`, behind the same auth and the same Postgres audit log. Safeguards:
   - validation before applying;
   - Railway's `/health/live` gate before traffic switches;
   - rollback.

   The Railway dashboard stays the break-glass way in if the gateway cannot boot at all.
7. **Platform tokens are entered once in the console** and encrypted in Postgres with the existing
   vault. They are write-only and audited.
8. **A bad deploy is rolled back automatically after 5 minutes.** After a change, if `/health/ready`
   (or the service's deployment status) stays unhealthy for 5 minutes, the system rolls back to
   the previous deployment, restores the variables it changed, and records why.
9. **Provider keys can be created as well as replaced** where the vendor has a key API:
   - Cloudflare: create and revoke.
   - The HF Space: both sides rotated together.
   - Groq: no public API, so replace the value only.
10. **Grafana Cloud: set up and status.** One action applies what `docs/deployment/observability.md`
    lists as manual steps (the `aura-gateway` scrape job, the alert rules, the two uptime checks),
    and the page shows their state and firing alerts.
11. **Qdrant Cloud: status and snapshots.** Health, collections and sizes, create and list
    snapshots, and restore one (restore needs step-up). No resizing.

## Safety design (applies to every stage)

**Who and how**
- **Every route is `require_super_admin`**, checked on the server for each request. The console's
  hiding of the page is convenience only.
- **Step-up, decision 3.**
  - `POST /api/v3/admin/control/step-up` takes a Google ID token obtained with `prompt=login` and
    `max_age=300`.
  - The server checks the console's client ID, the same subject as the caller's session, and an
    `auth_time` no older than 5 minutes.
  - It returns an opaque step-up grant: 5 minutes, bound to that subject and session family, stored
    server-side, revoked with the session.
  - Every write sends it as `X-Aura-Step-Up`. Refresh tokens cannot mint one, so a stolen session
    cookie cannot change anything.
- **Typed confirmation** names the exact target ("type `gateway` to redeploy"). The server
  re-checks it as a `confirm` field, so a script cannot skip the dialog.
- **Rate limit** on control writes: 30 per hour per Super Admin.

**Secrets**
- **Platform tokens** live in `EncryptedCredentialVault`. They are least-privilege by design:
  - a Railway *project* token for the production environment, not an account token;
  - an HF fine-grained token with write access to the one Space only;
  - a Grafana access-policy token with only the scopes the setup needs;
  - the existing Qdrant cluster key;
  - a Cloudflare token with "API Tokens: Edit".

  Each token is checked with one read call when saved, and refused if it reaches more than it
  needs where the platform can report its scope.
- **Secret variables are write-only end to end.** The API never returns a value. Platform
  variables are classified as secret by name pattern (`*_KEY`, `*_TOKEN`, `*_SECRET`,
  `*_PASSWORD`, `*_URL` with credentials, `*_KEYS`) and by the platform's own secret flag (HF).
- **The previous value of a replaced secret** is kept, encrypted in the vault, only for the
  auto-rollback window, then deleted.

**Not locking yourself out**
- **Gateway variables are validated before they are applied.** The candidate environment
  (current Railway variables plus the change) is loaded by `AuraSettings.from_env().validate()`
  in a **subprocess** with that environment. A change that would stop production from booting is
  refused with the validation message, before anything is written.
- **Protected variables** cannot be changed from the console; their rows say "change in the
  Railway dashboard":
  - `AURA_SECRETS_ENCRYPTION_KEY`, which would orphan the vault holding the platform tokens;
  - `AURA_DATA_ENCRYPTION_KEYS`, which would lose every user's encrypted data;
  - `AURA_DATABASE_URL`, which would cut the control plane off from its own journal.

  The JWT secret is changeable, with a warning that it signs everyone out.
- **Railway gates the switch on the health check:** a new gateway deployment receives traffic only
  after `/health/live` passes. A gateway that cannot boot leaves the old one serving, and the old
  one's reconciler sees the failed deployment.
- **Change journal plus reconciler (decision 8).**
  - Every change is a row in `aura_control_changes`: who, what (redacted), the previous state, the
    deployment it produced, status, and reason.
  - A reconciler runs in every gateway process; one holds a Postgres advisory lock at a time.
  - It follows each open change until its deployment is healthy for 5 minutes, or rolls it back:
    Railway rollback to the previous deployment, previous variable values restored with redeploys
    skipped, and the reason recorded.
  - Because it runs in whichever gateway is alive, it works whether the new deployment never came
    up or came up broken.
- **Every action is audited** through `audit_admin`: actor, action, target, outcome, never a value.
  Settings → Changes shows the journal.

## Backend (`aura_backend/`)

Hexagonal, as `tests/architecture/` enforces.

**Domain and ports** (stdlib only)
- `domain/control.py` holds the value objects:
  - `Platform`, `ServiceRef`, `VariableRef(name, secret, protected)`;
  - `VariableChange`, `ChangeRecord`, `ChangeStatus`, `DeploymentRef`.
- `ports/control_plane.py` holds one Protocol per platform capability: `DeploymentPlatform`
  (Railway), `SpaceHost` (HF), `ObservabilityStack` (Grafana), `VectorAdmin` (Qdrant),
  `KeyIssuer` (Cloudflare), and `ChangeJournal`.

**Engine: `aura_core/control/`**
- `ControlService` validates, applies, journals and rolls back.
- `settings_catalog.py` builds the **AURA settings view** from the settings dataclasses. For each
  `AURA_*` variable it gives: group, type, bounds, default, docstring, current effective value
  (redacted when secret), and whether a change needs a redeploy.
- `reconciler.py` is the auto-rollback loop.

**Adapters** (vendor calls inside functions, lazy clients, no network in `__init__`)
- `adapters/railway_platform.py`: Railway public GraphQL API (`backboard.railway.com/graphql/v2`,
  `Project-Access-Token`).
  - Services, variables, deployments, logs.
  - Variable upsert/delete with `skipDeploys`.
  - Redeploy, restart, rollback.
  - The exact mutation names are checked against the live schema's introspection before coding
    (stage 2's first step).
- `adapters/hf_space_host.py`: `huggingface_hub.HfApi`, for runtime/stage, pause/restart,
  hardware, sleep time, variables/secrets and logs.
- `adapters/grafana_cloud.py`: three Grafana APIs.
  - The Mimir ruler API loads `deploy/monitoring/alerts/aura.yml` as-is.
  - The Synthetic Monitoring API holds the two checks.
  - The scrape-job API (Metrics Endpoint integration) is a **research spike in stage 5**. If
    Grafana exposes no API for it, the page shows the exact values to paste once and then reads its
    status.
- `adapters/qdrant_admin.py`: the cluster's own API with the existing key, for health,
  collections and snapshots (create, list, restore).
- `adapters/cloudflare_tokens.py`: Cloudflare API tokens, create and revoke, scoped to Workers AI.
- `adapters/postgres_control.py`: the journal and the platform-credential index (which
  `secret_ref` belongs to which platform).
- **Every port also gets an in-memory adapter** with real state, so the default `pytest` run needs
  no network (CLAUDE.md).

**API: `aura_core/api/admin_control.py`**
- Everything lives under `/api/v3/admin/control/*`.
- Every route is `require_super_admin`, audited, and rate-limited.
- Writes require the step-up grant and `confirm`.

**Migration `0031_control_center`**, additive:
- `aura_control_platforms`: platform, secret_ref, configured_by/at, verified_at, scope_note.
- `aura_control_changes`: the journal.
- `aura_control_step_up`: grant hash, subject, session family, expiry.
- It has a real `downgrade()`, and the round-trip test covers it.

**ADR-037 "Console infrastructure control plane"**: records why the gateway holds infrastructure
tokens, what the step-up, protected-variable, journal and rollback rules protect against, and the
break-glass path.

**Tests**
- Step-up:
  - refused without a grant;
  - refused when `auth_time` is old;
  - refused when the subject is wrong;
  - a grant does not survive revoking its sessions.
- Non-SuperAdmin callers get 403 on every route (a parametrized sweep of the router).
- Validation:
  - a variable that breaks production boot is refused and nothing is written;
  - a protected variable is refused.
- Secrets never appear in any response or audit row: a sweep with a canary value.
- The reconciler:
  - an unhealthy change is rolled back after 5 minutes (FrozenClock) and its variables restored;
  - a healthy one is closed;
  - two processes never both act, because of the lock.
- Adapter contract tests against recorded HTTP exchanges (`httpx.MockTransport`), with no live
  calls in the default suite.
- An opt-in `tests/load/test_control_live.py` (`-m live`) runs read-only calls against the real
  platforms with real tokens.

## Console (`aura_admin/`)

**Route and gating**
- `/settings` is the control center. The nav item is shown only when `me.is_super_admin`.
- For anyone else the page says "Only the Super Admin can open Settings" and calls nothing.

**Tabs** (one route each, so they can be bookmarked; on a phone they collapse to a select)
1. **Overview**: one card per platform (connected, healthy or failing, last change), plus open
   changes being watched.
2. **AURA settings**: the catalog, grouped (LLM, knowledge, voice, vision, security, …).
   - Each row shows the value or "secret: set", the default, the description, and whether a change
     needs a redeploy.
   - Edits collect into a "pending changes" tray: validate → review diff → step-up → apply →
     redeploy.
3. **Railway**: services with status.
   - Per service: variables (secrets write-only), deployments with status, deploy and runtime logs,
     and Redeploy / Restart / Roll back to this deployment.
4. **GPU Space**: stage, hardware, sleep time, Pause/Resume/Restart, variables/secrets, logs.
5. **Keys**: each provider slot with key set/not set, provider health, Replace, and Create/Revoke
   where the vendor supports it. The HF key rotates both sides.
6. **Monitoring**: Grafana connection, an "Apply setup" button with a per-item result, firing
   alerts, and uptime checks.
7. **Vector DB**: Qdrant health, collections and sizes, snapshots (create, list, restore).
8. **Changes**: the journal, with status, who, when, what (redacted), deployment, and rollback
   reason.

**Setup cards.** A platform with no token shows a "Connect" card: paste the token, step-up, verify.
It explains the exact token type and scope to create.

**Shared components**
- `StepUpDialog`: a Google popup with `prompt=login`. The grant is held in memory only, never in
  `localStorage`.
- The existing typed-confirm dialog.
- `SecretField`, which can be written and never displays a value.
- `ChangeTray`.

**Other**
- Copy in `src/lib/i18n/{en,ar}.ts`, laid out RTL with logical properties, as the console already
  does.
- `tests/e2e/mock-gateway.mjs` gains the control routes with the same gates. New specs:
  `control-gating.spec.ts`, `control-railway.spec.ts`, `control-settings.spec.ts`,
  `control-space.spec.ts`, `control-keys.spec.ts`, `control-monitoring.spec.ts`,
  `control-arabic.spec.ts`.

## Contract

- New routes only, under `/api/v3/admin/control/*`. `/admin/status` is unchanged, so its other
  readers keep working.
- Behaviour change (decision 1): Admins no longer see the Settings page.

## Stage 1 as built (2026-11-21)

Built and tested locally, not proven (ADR-037, GAP-067). Where it differs from the design above,
this section wins:

- **Key name.** The vault key is `AURA_SECRETS_ENCRYPTION_KEY` (singular). An earlier draft of
  this plan said `..._KEYS`.
- **Routes.** All are `POST` or `GET`, because the console proxy (`app/api/v3/[...path]/route.ts`)
  exports no `PUT`:
  - `GET overview`, `GET settings`, `GET changes`;
  - `POST step-up/challenge`, `POST step-up`, `POST step-up/revoke`;
  - `POST platforms/{platform}/credential`, `POST platforms/{platform}/disconnect`.
- **Challenge first.** A step-up starts with a one-time nonce from `step-up/challenge`, which
  the Google ID token must carry. A token cannot be replayed for a second grant.
- **Grant binding.** A grant is bound to the subject and its email, not to a refresh-token
  family: the gateway's Google re-sign-in has no session-family handle. Instead, sign-out calls
  `step-up/revoke` (from `app/api/auth/logout/route.ts`), and the grant lives only in the tab's
  memory.
- **Refusals are 403 with `details.reason`**, never 401, because the console proxy turns a 401
  into a refresh and a redirect to the login page.
- **Platform target.** `aura_control_platforms` has a `target` column (the HF Space `owner/name`,
  the Qdrant cluster URL, the Grafana stack) instead of `scope_note`. It is typed in the Connect
  form. The Qdrant URL must be on `*.qdrant.io` or be `AURA_QDRANT_URL`'s host.
- **Tables.** Migration `0031_control_center` adds a fourth table, `aura_control_leases`, so one
  gateway process runs the reconciler.
- **Verification.**
  - HF, Cloudflare and Qdrant tokens are checked against the vendor before they are saved.
  - Railway and Grafana tokens are saved "not verified yet"; their clients come in stages 2 and 5.
  - The Cloudflare API root comes from the Workers AI provider's `base_url` (ADR-030: no vendor
    host in Python source).
- **File names.**
  - The engine is `control/catalog.py`, `control/service.py` (StepUpService, ControlService,
    Reconciler) and `control/wiring.py`.
  - The adapters are `adapters/control_store.py` (in-memory and Postgres),
    `adapters/identity_reverify.py` (Google) and `adapters/platform_verify.py`.
  - The per-platform ports (`SpaceHost`, `KeyIssuer`, …) arrive with their stages. Stage 1 has
    `ControlStore`, `IdentityReverifier`, `PlatformVerifier` and `DeploymentPlatform`.
- **Not started yet.** The Reconciler is built and tested but the gateway does not run it. No
  stage-1 action produces a deployment, so stage 2 starts it.
- **Sign-in time.** Google never sends `auth_time` (not in its `claims_supported`), so the
  freshness check falls back to `iat`. The one-time nonce keeps that sound: a token cannot
  predate its challenge. What is lost is proof that the password was typed again.
- **Setup the owner must do.** Add both `https://console.aureon-ai.tech/console/step-up/callback`
  (while the console still runs under `/console`) and `https://console.aureon-ai.tech/step-up/callback` as
  Authorized redirect URIs on the console's Google OAuth client, and make sure
  `AURA_SECRETS_ENCRYPTION_KEY` is set on the Railway gateway.

## Stage 2 as built (2026-11-22)

Built and tested locally, not proven (ADR-037, GAP-067). Where it differs from the design above,
this section wins:

- **Railway API, checked first.** Every query and mutation was checked against Railway's live
  schema (introspection, then each document sent with a dummy token: every answer was an auth
  or "not found" error, never a schema error). Requests use `httpx`, since Cloudflare in front
  of Railway refuses Python's `urllib` (error 1010).
- **Scope from the token.** The project and environment come from the token's own
  `projectToken`. Nothing else is configured, and the token check on Connect is the same query.
- **One redeploy per change.** Variables are written with `skipDeploys: true`, then one
  `deploymentRedeploy(usePreviousImageTag: true)`: same image, new variables, no rebuild. A
  rebuild would outlast the 5-minute watch.
- **Validation.** It runs `AuraSettings.from_env().validate()` in a child process with exactly
  the candidate environment, plus the gateway's JWT-secret rule, for any service with an
  `AURA_*` variable. Other services, such as the console, skip it.
- **Extra refusals, beyond the design:**
  - `${{...}}` references (set them in the Railway dashboard);
  - `RAILWAY_*` variables;
  - a second change while one is watched;
  - an AURA service with no health-check path.
- **Restore values are raw.** A rollback restores references as written, not their rendered
  values.
- **The reconciler is started** by the gateway's startup hook in staging/production
  (`AURA_CONTROL_RECONCILER`, default on).
  - A deployment replaced by a newer one is closed without a rollback.
  - A rollback you choose is journaled, not watched.
- **AURA settings tab.** It reads, and edits through the same tray, the gateway's own Railway
  variables (`RAILWAY_SERVICE_ID`), once Railway is connected. Each row shows the value Railway
  holds when it differs from the running one ("next deploy").
- **Console e2e.** One spec file (`control-center.spec.ts`) holds both stages, because the mock
  gateway's control state is shared and Playwright keeps one file's tests on one worker.
- **Not built yet.** Stage 2 has no live contract test (`tests/load/test_control_live.py`); the
  owner's live checks below stand in for it.

## Delivery: five stages, each its own PR, each usable on its own

| Stage | Contents | Proof needed before calling it done |
|---|---|---|
| **S1 Foundation** | Migration 0031; vault-backed platform tokens; step-up; journal, reconciler and audit; Super-Admin-only Settings with Overview, AURA settings (read) and Changes; ADR-037 | Live: Super Admin opens Settings and an Admin is refused; step-up round trip with real Google |
| **S2 Railway + AURA settings edit** | Railway adapter; variables with validation; redeploy, restart, rollback; logs; auto-rollback | Live: change a harmless variable on the gateway → redeploy → healthy; change a variable that fails readiness → automatic rollback within 5 min |
| **S3 GPU Space** | HF adapter and tab | Live: pause/resume the Space; change sleep time |
| **S4 Keys** | Replace; HF both-sided rotation; Cloudflare create and revoke; provider health check after rotation | Live: rotate the HF key end to end with no downtime |
| **S5 Monitoring + Vector DB** | Grafana apply/status (with the scrape-job spike); Qdrant snapshots | Live: apply the Grafana setup and see `up{job="aura-gateway"}`; create and list a Qdrant snapshot |

## Verification

- Backend: `pytest`, `pytest tests/architecture -q`, legacy `unittest`, and migration 0031 up,
  down and up on a throwaway Postgres 16.
- Console: `npx tsc --noEmit`, `npm run lint`, `npm run build`, `npm run test:e2e`.
- Manual (owner), per stage: the "proof" column above, in English and Arabic, on a desktop and a
  phone browser.

## Out of scope

- The VPS (its deploy path is dead).
- Deploying arbitrary branches (decision 4).
- Creating or deleting Railway projects or services.
- DNS (Hostinger).
- Billing or plan changes on any platform.
- Resizing Qdrant (decision 11).
- Creating Groq keys (no vendor API).
- A separate control service (decision 6).
