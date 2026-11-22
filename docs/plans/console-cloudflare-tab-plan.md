# Plan: a Cloudflare tab in the console's Settings, with provider add, replace and remove

**Status:** Proposed, awaiting approval · **Date:** 2026-11-23 · **Owner:** platform-core ·
**Builds on:** ADR-037 and `docs/plans/console-control-center-plan.md` (stages 1-5, built, not
proven; GAP-067) · **Related:** ADR-030 (hosted open-weight inference), ADR-038 (`clairic`, not
built)

## Goal

Give the Super Admin one place in the console, **Settings -> Cloudflare**, to run everything about
the account's Workers AI use that the control center can safely run:

1. connect, replace or disconnect the Cloudflare token, with the account it belongs to;
2. see and manage the Cloudflare API tokens (list, create, revoke);
3. browse Cloudflare's live Workers AI **model catalog**, with what AURA will and will not admit;
4. **add, replace and remove Cloudflare providers (models)** that the gateway serves from;
5. see **today's Workers AI usage** against the free allowance;
6. see the **R2 backup bucket status**, read-only.

Everything that changes the gateway goes through the control center's existing path: validate
the candidate environment in a child process, write the variables, redeploy once, watch for five
minutes, roll back automatically if unhealthy.

## User decisions (do not re-ask)

Recorded 2026-11-23, in addition to the control-center decisions of 2026-11-21 (Super Admin only,
step-up plus typed confirmation, secrets write-only, validate-apply-watch-rollback).

1. **Token family.** The console's Cloudflare token is a **user-owned** token (`cfut_`). The
   account-owned token (`cfat_`) is refused by the user-token API the adapter calls (probed
   2026-11-22: `/user/tokens/verify` 401, `/user/tokens` 403). No adapter change to support
   account-owned tokens.
2. **Extra views.** Usage today and R2 backup status, both in the new tab.
3. **Removing a provider revokes its token if unused.** The token is revoked only when no other
   provider uses it and it is a user token the console manages. If the change is rolled back, the
   token is not revoked.
4. **Consent flag.** If `AURA_ALLOW_HOSTED_INFERENCE` is off, the Add dialog shows the consent text
   with a checkbox and sets the flag in the same change as the new provider.
5. **Form fields.** Essentials plus quotas: model (from the live catalog), quality tier, context
   window, max output tokens, on or off, requests and tokens per minute and per day, output speed,
   JSON mode and vision. The provider name is fixed once added. Data ceiling, pools and workloads
   stay in the AURA tab.

Defaults taken from the control-center plan, not asked again: a new provider's key is created for it
(a new scoped user token, written to the gateway) with "paste an existing key" as the alternative
(decision 9); every string in English and Arabic with right-to-left layout.

## Verified facts this plan rests on

Probed against Cloudflare on 2026-11-22 and 2026-11-23 with a token that is being rolled; to be
re-confirmed with the user token in step B1.

- **Model catalog:** `GET /accounts/{id}/ai/models/search` works (about 336 models). Each model has
  `id`, `name`, `description`, `task {id,name}`, `created_at`, `tags` and `properties`
  (`context_window`, `price`, `vision`, `function_calling`, `reasoning`, `reasoning_effort`,
  `require_workers_paid`, `terms`, `async_queue`, `lora`). Cloudflare's published docs do not
  document this endpoint, so the response shape is treated as unstable and parsed defensively.
- **Health path:** `GET /accounts/{id}/ai/v1/models` answers **405**. The router's default active
  probe would mark every Cloudflare provider unhealthy, so providers this tab writes use
  `AURA_PROVIDER_<NAME>_HEALTH_PROBE=passive`.
- **Usage:** the GraphQL dataset `aiInferenceAdaptiveGroups` answers; its neuron field name is
  **not yet confirmed**. If there is none, the view shows request counts and says so.
- **R2:** `GET /accounts/{id}/r2/buckets` answered **10042, "enable R2 through the Cloudflare
  Dashboard"**. This account has R2 disabled, so the view must show that as a first-class state.
- **Free allowance:** 10,000 neurons per day, reset 00:00 UTC; Cloudflare says calls then "fail
  with an error" (status code unverified).
- **Provider variables** follow `AURA_PROVIDER_<NAME>_*`; the key convention on Railway is
  `AURA_PROVIDER_<NAME>_API_KEY_ENV=AURA_PROVIDER_<NAME>_API_KEY`. A change may carry up to 50
  variables, 32 KiB each; a provider needs about 20.

## Backend (`aura_backend/aura_core`)

Layering as the project defines it: domain and ports stdlib only, engines depend on ports,
adapters hold the HTTP, `container.py` wires.

**Domain** (`domain/control.py`)
- `CloudflareProviderSpec` (frozen, validated on construction): name (`[a-z][a-z0-9-]{0,31}`,
  unique), model, quality tier 1-3, context window, max output tokens, enabled, optional rpm/rpd/
  tpm/tpd, tokens per second, JSON mode, vision. Unknown or out-of-range values are refused, never
  coerced.
- `provider_variables(spec, account_id, key_env) -> dict[str, str]`: a pure function that
  produces the exact variable set (kind `openai_compatible`, base URL, model, key variable name,
  `HEALTH_PROBE=passive`, quotas, modalities), so its output can be tested against the real
  settings loader.
- `PLATFORM_TOKEN_HINTS[CLOUDFLARE]` updated: a user token with `API Tokens: Edit`, plus account
  `Workers AI: Read` and `Account Analytics: Read`, and optionally `Workers R2 Storage: Read`.

**Ports** (`ports/control_plane.py`)
- New `CloudflareAccount` protocol: `models(account_id, ...)`, `usage(account_id, day)`,
  `r2_buckets(account_id)`. `CloudflareTokens` is unchanged.

**Adapters**
- `adapters/cloudflare_account.py`: `HttpCloudflareAccount` (httpx, lazy, 15 s timeout, vendor
  messages never passed through, 401/403/404/405/10042 mapped to named states) and
  `InMemoryCloudflareAccount` with real state.
- `adapters/platform_verify.py`: the Cloudflare verifier also checks `Workers AI: Read` by one
  cheap catalog call and records the account id as the credential's non-secret `target`.
- The Cloudflare API address is derived from a named constant in `config/external_inference.py`
  (the single host registry), not from an already-configured provider. That is what lets the
  **first** provider be added; today with none configured nothing can connect.

**Engine** (`control/cloudflare.py`, new `CloudflareControl`)
- `overview()`: connection state, account id, flags (`AURA_ALLOW_HOSTED_INFERENCE`), providers
  with health, quota, data ceiling and key state.
- `models(...)`: the catalog annotated per model with `chat` (task is Text Generation),
  `admitted` (`is_open_weight_model`), `paid_only`, `vision`, context window, price and the
  refusal reason when not admitted.
- `validate_provider(...)`: a dry run returning the variable changes and the child-process
  verdict, nothing written.
- `add_provider`, `replace_provider`, `remove_provider`: each builds the variable changes and
  applies them as **one** change through `RailwayControl.apply_variables`.
  - **Add:** appends the name to `AURA_LLM_PROVIDERS` (before `local`), writes the provider's
    variables, optionally `AURA_ALLOW_HOSTED_INFERENCE=true`, and creates its key (or takes a
    pasted one). A key created for the change is revoked if the change cannot be applied.
  - **Replace:** changes the model and the form fields on an existing Cloudflare provider; may
    also create a new key.
  - **Remove:** removes the name from `AURA_LLM_PROVIDERS`, deletes the provider's variables and
    its key variable. Refused if it is the last provider. The token is revoked only after the
    change **succeeds** (see below).
- Only providers whose host is the Cloudflare host are listed or touched. Groq, the GPU Space
  and any other slot are not editable from this tab.
- `usage()` and `r2()` read through the port and return named states (`ok`, `not_enabled`,
  `no_permission`, `unavailable`).

**Revoke after success (new mechanism).** The reconciler today restores things on a rollback
(`__companion__`) but has no hook on success. A change may carry
`__on_success__: {"action": "cloudflare_revoke", "token_id": ...}` in its vault-held restore values;
`Reconciler._close(SUCCEEDED)` performs it, re-checks that no provider still uses the token, and
journals the result; a rolled-back or failed change discards it. A revoke that fails is journaled
and leaves the token visible in the tokens table with a clear message.

**API** (`api/admin_control.py`, all `require_super_admin`, writes need step-up and the server
re-checks the typed confirmation)
- `GET /admin/control/cloudflare`, `GET .../cloudflare/models`, `GET .../cloudflare/usage`,
  `GET .../cloudflare/r2`
- `POST .../cloudflare/providers/validate`, `POST .../cloudflare/providers`,
  `POST .../cloudflare/providers/{name}/replace`, `POST .../cloudflare/providers/{name}/remove`
- Existing routes reused unchanged: `POST /platforms/cloudflare/credential` and `/disconnect`,
  `GET /keys/cloudflare/tokens`, `POST /keys/cloudflare/tokens/{id}/revoke`.
- The credential route accepts the account id as `target` for Cloudflare.

**Migration:** none. Tokens use the existing vault and credential rows; changes use the existing
journal.

**Tests** (default `pytest`, no network)
- Domain: spec validation table, `provider_variables` output, and **the output passes the real
  `AuraSettings.from_env().validate()`** (including the hosted flag, open-weight and health rules).
- Engine: add, replace and remove against `InMemoryRailway` and `InMemoryCloudflare*`; consent
  on and off; refusal of non-admitted models, duplicate and reserved names, the last provider and
  non-Cloudflare slots; key creation revoked on a failed apply; revoke only after success, not on
  rollback, not when another provider shares the token.
- Adapter: `HttpCloudflareAccount` over recorded exchanges (catalog, 405, 10042, 401/403, an
  unexpected shape).
- Control-center sweeps: the existing role sweep and OpenAPI completeness check cover every new
  route (Admin and below get 403, no step-up gets 403), the canary sweep proves no token or key
  value appears in any response, audit event or journal row.
- Architecture: no Cloudflare host named in source outside the registry; `HEALTH_PROBE=passive`
  is always written for these providers.

## Console (`aura_admin`)

Flow as the console does it today: types in `src/lib/control.ts`, a page per tab, the shared
`StepUp` and `ConfirmDialog`, tokens from the design system.

- **New tab** `app/(console)/settings/cloudflare/page.tsx`, added to `TABS` after Keys.
- **Sections:** Connection (status, account id, connect or replace or disconnect), Providers
  (table: name, model, health, key state, limits, with Add, Replace and Remove), Models (catalog
  with search and filters for task, vision, paid-only and "AURA can use"; refused models show
  why), Usage (today against 10,000, or request counts when there is no neuron figure), Tokens
  (the table moves here from Keys, which keeps a link), R2 (bucket list, lock rule when the API
  gives it, and a clear "R2 is not enabled on this account" state).
- **Add and Replace dialogs:** pick a model from the catalog, fill the essentials and quotas,
  see the exact variable changes and the validation verdict before applying, choose key
  (create or paste), tick the consent box when the flag is off, type the provider name to
  confirm. Remove shows what will be deleted and that the token will be revoked if unused.
- **Change tray:** each action shows the watched change like the other tabs do.
- **Overview card:** the Cloudflare connect form asks for the account id with the token.
- **i18n:** every new string in `en.ts` and `ar.ts`; right-to-left layout checked; model ids,
  variable names and numbers stay left-to-right.
- **Tests:** mock-gateway handlers and Playwright cases for connect, catalog filter, add
  (with and without consent), replace, remove with token revoke, usage, R2 not enabled, a
  token or key never appearing in the DOM, and an Arabic layout pass.

## Contract

Additive only. New routes under `/admin/control/cloudflare/*`; the overview and credential
responses gain optional fields (`cloudflare_account_id`); no existing field, route or enum value
changes. The console is the only consumer and ships from the same repository.

## Rollout order

1. Backend merged to `main`: Railway auto-deploys the gateway (the new routes are inert until
   the tab calls them).
2. Console in the same merge or right after: until both are deployed, the tab shows its error
   state rather than breaking other tabs.
3. Owner steps (not code): create the user-owned Cloudflare token with the permissions above,
   connect it in Settings, then use the tab. Roll the exposed account token.

## Verification

Per layer, with this machine's limits stated (Python 3.14, not the locked 3.11; no reliable CI):

- Backend (`aura_backend`): `python -m pytest tests/unit/test_control_cloudflare.py
  tests/unit/test_control_center.py tests/unit/test_control_keys.py tests/architecture -q`, then
  the full `pytest`, and `TESTING=True python -m unittest discover -p "test_*.py"`.
- Console (`aura_admin`): `npx tsc --noEmit`, `npm run lint` (warnings fail), `npm run build`,
  `npm run test:e2e`.
- Regenerate `docs/guides/api-reachability.md`.
- Manual test by the owner (English and Arabic, desktop and phone), then the proof in the exit
  condition below.

## Docs

ADR-037 amendment ("stage 6: the Cloudflare tab"), this plan, GAP-067 text, `BACKEND_STATUS.md`,
`WEB_CONSOLE_STATUS.md`, `docs/deployment/control-plane.md` (token permissions, passive health
probe) and `deploy/.env.example`.

## Work packages

| # | Package | Depends on |
|---|---|---|
| B1 | Confirm catalog, usage fields and token permissions with the user token; adapter and port | owner connects token |
| B2 | Domain spec, `provider_variables`, hints, host constant | - |
| B3 | `CloudflareControl` engine: overview, models, validate, add, replace, remove | B1, B2 |
| B4 | Revoke-after-success hook in the reconciler | B3 |
| B5 | API routes, wiring, role and canary sweeps | B3 |
| C1 | Types, i18n (en, ar), tab shell, Connection and Tokens | B5 |
| C2 | Providers, Models, Add, Replace and Remove dialogs | C1 |
| C3 | Usage and R2 views | C1 |
| C4 | Mock gateway and Playwright tests, Arabic pass | C2, C3 |
| D1 | Docs, status files, reachability matrix | all |

B1 needs the owner's user token, so B2-B5 and the console work proceed against recorded
exchanges and the in-memory adapters first.

## Risks and open assumptions

- **Nothing here has run against real Cloudflare or Railway.** The control center is built, not
  proven (GAP-067); this stage inherits that.
- **The catalog endpoint is undocumented.** It works today; the parser must tolerate a changed
  shape and the tab must say so rather than show an empty list.
- **Usage and R2 depend on extra token permissions** (`Account Analytics: Read`, `Workers R2
  Storage: Read`). The token stored in the vault gets wider reach; both are optional, and the
  views say "no permission" when absent. Least privilege is a trade-off the owner accepts per
  permission.
- **Consent from the dialog.** Setting `AURA_ALLOW_HOSTED_INFERENCE` is a deliberate consent that
  a third party reads prompts; the dialog states that in plain words and the journal records it.
- **Existing Cloudflare slots on the default health probe** may already show unhealthy (405).
  Replace sets the passive probe; whether a live slot is affected is unchecked.
- **Per-process caps.** Any call counter is per worker.
- **Revoke-after-success** adds a new reconciler path; a failed revoke leaves a visible, working
  token rather than a hidden one.

## Out of scope

DNS and zones (the domain is at Hostinger), billing and plan changes, AI Gateway (its host is
banned), Zero Trust, Workers scripts, R2 object operations and changing R2 lock rules, creating or
deleting R2 buckets, editing Groq, GPU Space or Claude providers from this tab, supporting
account-owned tokens in the adapter, and the `clairic` extraction settings (ADR-038 is not built;
they join this tab when it is).

## Exit condition (proven, not just built)

`docs/reports/console-cloudflare-tab-*.md` committed showing, on the live console: (1) the
Cloudflare token connected with the account id and no response containing it; (2) the catalog
listing with refusals explained; (3) one provider added, healthy after the watched deploy, then
replaced to another model, then removed with its token revoked; (4) usage shown against the
allowance; (5) the R2 view on an account with R2 enabled and the "not enabled" state; (6) the
tab in English and Arabic.
