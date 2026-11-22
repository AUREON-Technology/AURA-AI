# ADR-037 — The console's Super-Admin control center: infrastructure tokens held by the gateway

**Status:** Proposed (stages 1-5 built, not proven) · **Date:** 2026-11-21 · **Related:** ADR-002
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
     redirect goes to `<base path>/step-up/callback`, which posts the ID token back to its opener
     on the same origin only.
  3. The gateway checks the token through `GoogleReverifier`. It needs one of our `aud` values,
     `email_verified`, our nonce, the same email as the session, and a sign-in time within
     `max_age`: `auth_time` when the issuer sends it, otherwise `iat`. Google never sends
     `auth_time` (its discovery document leaves it out of `claims_supported`), so in practice
     the check is on `iat`. That is still a freshness proof, because a token cannot be older than
     the one-time nonce it carries.
  4. It returns an opaque five-minute grant, stored only as SHA-256.

  The console holds the grant in memory only (never `localStorage` or a cookie) and sends it in
  `X-Aura-Step-Up`. A stolen session cookie cannot buy one: buying one needs the person's live Google
  account *now*. The console asks Google to re-prompt for the password (`prompt=login`), but
  nothing in a Google token proves it did, so the server-side guarantee is "control of the
  Google account within the last five minutes", not "password typed again". Found on the first
  live try (2026-11-21), when the original `auth_time`-only check refused every real sign-in. Sign-out calls `POST /admin/control/step-up/revoke`, so a
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
  - Railway: the `projectToken` query, which also tells the gateway which project and
    environment the token reaches. An account or team token is refused.
  - Grafana is saved "not verified yet" until stage 5 adds its client.

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
- **A variable change has to pass, before anything is written** (stage 2, `control/railway.py`):
  - the typed service name and a step-up grant;
  - no other change to that service still being watched, so a rollback can never undo the
    wrong change;
  - not a protected variable, not one of Railway's own `RAILWAY_*`, and no `${{...}}`
    reference (its rendered value is unknown until Railway renders it, so it cannot be
    checked);
  - for a service that runs AURA (any `AURA_*` variable): a health-check path on the service,
    or Railway would switch traffic to a deployment that never proved it boots; and the whole
    candidate environment must pass `SettingsValidator`. That loads `AuraSettings` and runs
    `validate()` in a **child process** whose environment is exactly the candidate, plus the
    gateway's JWT-secret rule. A redeploy reuses the image, so this is the code that will boot.

  Then the variables are written with `skipDeploys`, and exactly one redeploy of the current
  image (`deploymentRedeploy`, `usePreviousImageTag`) is started. Its id goes in the journal,
  with the previous raw values (references kept as written) in the vault. If the redeploy
  cannot start, the old values are put back and the change is journaled `FAILED`.
- **The reconciler runs in every staging/production gateway process** (`AURA_CONTROL_RECONCILER`,
  default on), started by the gateway's startup hook, never by a Celery worker. Railway's
  status words map to health: `SUCCESS` (health check passed) is healthy, `FAILED`/`CRASHED`
  failed, `REMOVED`/`SKIPPED` superseded. A change whose deployment was replaced by a newer one
  (a merge to `main`, say) is closed without a rollback, which would undo the newer deployment
  too.
- **A rollback you choose is journaled, not watched.** Undoing it automatically would only pick
  between two states a person has already rejected.
- **Logs are masked:** every secret value of the service is replaced before a log line is
  returned.
- **The GPU Space (stage 3, `control/space.py`) is the Space its token was connected for**
  (the HF credential's `owner/name`), so no setting points the token anywhere else. The Hub
  restarts a Space by itself whenever a variable or secret changes, so the rules differ from
  Railway's:
  - **Variables** are checked, written and watched with the Space's own window
    (`AURA_CONTROL_SPACE_WATCH_S`, 20 minutes by default), because a rebuild reloads the model
    on the GPU. A failed or still-not-running Space gets its old values back, which restarts
    it. A variable whose name or value looks secret is refused: it belongs in the Space's
    secrets.
  - **Secrets** are write-only, as on the Hub, and journaled as not restorable: the Hub never
    returns a value, so nothing can be put back. The console says so before every change.
  - **Hardware** (billed per hour), sleep time, pause, restart and rebuild are journaled, not
    watched. Rolling back hardware automatically could put the Space back on the hardware
    that was failing.
  - The reconciler reads the Space's stage: `RUNNING` is healthy, `*_ERROR` failed, and
    `PAUSED`/`STOPPED` superseded (someone stopped it on purpose).
- **Provider keys (stage 4, `control/keys.py`) are gateway variables.** Every key change is
  therefore a Railway variable change on the gateway itself, with all of stage 2's checks,
  its one watched redeploy and its rollback.
  - **Replace** works for any slot, with a value you paste. Groq has no key API.
  - **Rotate both sides** is for the GPU Space. vLLM checks one `--api-key`, so the old and new
    keys cannot overlap.
    - The gateway generates the new key and sets it as the Space's `VLLM_API_KEY`, which
      restarts the Space. It then writes the same key to the gateway variable.
    - The gateway redeploys faster than the Space reloads its model, so the gap is the Space's
      restart, which any Space change costs. The router fails over meanwhile.
    - If the gateway change is refused, the Space gets the old key back at once. If the change
      is rolled back later, the reconciler puts it back through a *companion restore* kept in
      the vault with the change. The old key is known because it is the gateway's own variable.
  - **Create** is for a Workers AI slot. The stored Cloudflare management token creates a token
    with the Workers AI read and write groups, scoped to the account the slot's URL names; the
    gateway then switches to it.
    - A switch that cannot start revokes the token it made.
    - The old token is revoked as a separate step, and the token in use cannot be revoked.
  - Slots come from each provider's `api_key_env`. A Workers AI slot is recognised by its URL,
    and the Space by the hostname the Hub derives from its `owner/name`, so no host is named in
    source.
  - No key value is returned, journaled or audited.
- **Grafana Cloud (stage 5, `control/monitoring.py`): set up and status.**
  - **Alert rules.** "Apply setup" loads every group of `deploy/monitoring/alerts/aura.yml` into
    the stack's ruler (namespace `aura`), one YAML group per request, and reports each group.
    A partial apply is journaled `FAILED`, naming the groups that did not load.
    - The gateway image is built from `aura_backend/` alone, so it carries a copy of the rules
      file, and a test keeps that copy byte-identical to the one in `deploy/`.
    - The stack comes from grafana.com's instances API, called with the access policy token.
    - The hosted Prometheus takes basic auth (its instance id, plus the token) and serves
      queries under `/api/prom`.
    - Grafana Cloud's ruler path prefix is not documented, so the documented candidates are
      tried in order and the one that works is remembered.
  - **What no API covers for this token:** the Metrics Endpoint scrape job (UI or Terraform
    only) and the Synthetic Monitoring checks (which need their own token). The page gives
    their exact values to enter once, then reads whether each works: `up{job="aura-gateway"}`
    and the probe success rate. It also lists firing `ALERTS`.
  - A query that cannot run is reported as such, never read as "all clear".
- **Qdrant Cloud (stage 5, `control/vector.py`): status and snapshots.**
  - Health, collections and snapshots come from the cluster API, with the stored key.
  - A restore first snapshots the current state, so the restore itself can be undone. It then
    recovers from the snapshot's file on the node, because Qdrant Cloud refuses recovery from
    a URL.
  - No resizing: that is a billing decision.
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
- **One manual setup step:** the callback must be an authorized redirect URI on the console's
  Google OAuth client, or the popup ends in `redirect_uri_mismatch` and no write can be made. The
  console builds it from its base path, so on Railway it is
  `https://console.aureon-ai.tech/console/step-up/callback` while `NEXT_PUBLIC_BASE_PATH` is still
  `/console`, and `https://console.aureon-ai.tech/step-up/callback` after the domain move removes
  it. Register both.
- **Schema:** migration `0031_control_center` adds four tables (`aura_control_platforms`,
  `aura_control_changes`, `aura_control_step_up`, `aura_control_leases`) with a working
  `downgrade()`. The migration round trip on PG16 covers it, and so does
  `tests/load/test_control_store_postgres.py`.

## Stages

| Stage | Scope | Built |
|---|---|---|
| S1 | Foundation: migration 0031; vault-backed tokens; step-up; journal, reconciler and audit; Settings with Overview, AURA settings (read-only) and Changes | yes (2026-11-21), not proven |
| S2 | Railway: services, variables (edit with validation), redeploy, restart, rollback, logs; the reconciler turned on; AURA settings edited through the gateway's own Railway variables | yes (2026-11-22), not proven |
| S3 | HF GPU Space: status, pause/resume, restart, rebuild, hardware, sleep time, variables (watched), secrets (write-only), logs | yes (2026-11-22), not proven |
| S4 | Provider keys: replace (any), rotate both sides (GPU Space), create and revoke (Cloudflare Workers AI) | yes (2026-11-22), not proven |
| S5 | Grafana Cloud (rules applied, scrape/uptime/firing status) and Qdrant Cloud (status, snapshots, restore with a safety snapshot) | yes (2026-11-22), not proven |

## Evidence still owed

Built, not proven until `docs/reports/control-center-*.md` is committed. It must show, on the
live console:

1. The Super Admin opens Settings, and an Admin is refused, both in the UI and at the API.
2. A real Google step-up round trip.
3. A token saved and verified, with no response containing it.
4. From stage 2 on: one change rolled back automatically after a forced unhealthy deploy.
