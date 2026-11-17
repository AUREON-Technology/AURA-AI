# ADR-034 — Workspaces: personal and team scopes for a multi-customer SaaS

**Status:** Proposed (built, not proven) · **Date:** 2026-11-16 · **Related:** ADR-002 (v2
platform adapters), ADR-006 (trust channels), ADR-029 (service principals), ADR-032 (per-user data
keys) · **Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md`

## Context

The owner is selling AURA as a SaaS, starting with individuals and small teams in Egypt (spec
§1). Until now every stored object belonged to exactly one person. Each storage port took an
explicit `owner_id`, which was always the caller's subject. Nothing could be shared, and a business
customer with several staff had no way to work from one corpus.

Platform roles (`services/rbac.py`: User … SuperAdmin) already exist, but they are the
**operator's**. They gate the console and `admin.*` capabilities, so reusing them for a customer's
"team admin" would hand a customer operator powers.

## Decision

### 1. Workspaces and roles

- Every person has one **personal** workspace.
- A **team** workspace has exactly one Owner, plus Admins and Members.
- Members join by a single-use emailed token, `auraiv_<id>_<secret>`. Only its SHA-256 is stored
  and it expires after 7 days.
- Workspace roles live in `domain/tenancy.py`. They never map onto platform roles: a workspace role
  adds no capability and opens no console route (`tests/unit/test_workspace_principal.py`).

### 2. The scope reuses `owner_id`

The ports keep their `owner_id` obligation; what changes is the value passed.

- **Personal:** the person's own subject, exactly as before. Existing data needs no rewrite.
- **Team:** `workspace:<32-hex id>` for **shareable** data: knowledge documents, the knowledge
  graph, the document list, CAG pins and prefix, the `search_knowledge` tool, and delegated
  sub-turns. `workspace:` is a reserved principal prefix, so no person's subject can collide with
  it.
- **Never shared:**
  - memory;
  - session history and goals;
  - Personal-OS records (tasks, calendar, finance, health) and their data keys;
  - feedback and agent runs;
  - Engineer workspaces (until the desktop-Engineer plan).

  These stay on the person's subject in every workspace, so a team admin cannot read them.

`Principal.knowledge_owner` is the single source of the shareable scope.
`tests/architecture/test_knowledge_scope_from_principal.py` fails if `api/v3.py` hands
`caller.user_id` to a knowledge, graph, CAG or document store.

### 3. The active workspace is a request header, checked on every request

`X-Aura-Workspace: <id>` selects a workspace; without it, the caller is in their personal scope and
the store is not consulted. With it, the gateway's authenticator looks the membership up on every
request, with no cache.

This departs from the spec's first sketch, which put the workspace in the JWT and re-checked it at
refresh:

- A removed member is refused on their very next request, instead of when their access token
  expires.
- The v2 token format is unchanged.

The cost is one primary-key lookup per team-scoped request, the same pattern service keys use.

"No such workspace" and "not a member" return the same 403, so the API never confirms that someone
else's workspace exists.

### 4. Inside a team, Members read and Owners/Admins write the shared corpus

Ingest, upload, delete and pin/unpin require `manage_knowledge` (Owner or Admin).

### 5. Everything keyed per person is also keyed per scope

- **Personal memory in team turns.** A team chat turn does not recall the person's private memory
  (spec §5.2), because teammates read the answer.
- **The response cache.** It is namespaced by person *and* scope, so an answer built from team
  documents is never served in the person's personal workspace, including after they leave the
  team.
- **`Idempotency-Key` claims.** They include the workspace, so the same key reused in a team does
  not replay the personal write.
- **Membership changes.** They are conditional on the role the decision was made against, so a
  concurrent ownership transfer can never be overwritten into a team with no Owner.

### 6. Who may create or join a team, and from which sign-in (SaaS P1-B)

- **Verified email.** Creating a team or accepting an invite requires a verified email
  (`Principal.email_verified`, carried on the access token from the identity provider's
  `email_verified`). Google and Apple sign-ins arrive verified; an email/password account must
  verify first.
- **Customer web sessions.** They come from `/api/v3/auth/consumer/token` and are capped at the
  `User` platform role (`channel=consumer_web`). Workspace roles never change that.
- **Setup.** `docs/deployment/customer-sign-in.md` lists the setup steps for the Firebase-backed
  sign-in methods.

### 7. Plans and metering (SaaS P1-C)

- **One plan per workspace.** It comes from `aura_workspace_entitlements` (migration `0021`); no
  row means Free. The plan catalogue is data (`domain/plans.py`; `AURA_PLAN_CATALOGUE` replaces
  it).
- **Metering.**
  - `aura_usage_counters` checks the limit and increments in one upsert, so no two requests on any
    worker can both take the last unit.
  - The gateway reserves a message before a turn and gives it back if the turn fails. It reserves
    storage before an ingest and settles to what the store kept.
  - Team seats come from the plan (`PlanService.seat_limit`).
- **Refusals.** HTTP 402 `plan_limit`. Operators and service keys are unmetered. Operations guide:
  `docs/deployment/plans-and-limits.md`.

### 8. Subscriptions and billing (SaaS P1-D1)

- **Ownership.** AURA owns each workspace's subscription (`aura_subscriptions`, migration `0022`).
  Fawry only collects payments (`aura_payments`).
- **Notifications.** A payment leaves `pending` exactly once (a conditional UPDATE), so a duplicate
  or out-of-order provider notification is a no-op.
- **What changes the plan.** A paid payment activates or extends the subscription and sets the
  plan with `source="subscription"`. A wrong amount activates nothing.
- **The hourly cycle.** It renews, moves unpaid subscriptions past due → grace (full access) →
  expired (Free), and reconciles pending payments with Fawry.
- **Secrets.** The Fawry secure key is read from the environment through a property, never a
  settings field. Operations guide: `docs/deployment/billing-fawry.md`.

## Consequences

- Tables `aura_workspaces`, `aura_workspace_members` and `aura_workspace_invites` arrive with Alembic
  `0020`. A unique `personal_owner` column enforces one personal workspace per person, and a
  partial unique index enforces one owner per team, even under a race the code missed.
- What erasing a person's `users` row cascades to:
  - their memberships and their personal workspace;
  - never a team they created: `created_by` is history, not ownership. A team survives its creator
    once it has been handed over.
- A subject with no `users` row yet gets one created on first use (`ensure_user_id`), so these
  calls never return a 500 for a valid subject.
- Still not covered: voice turns run in the personal scope (no workspace header on the WebSocket),
  and deleting a team does not yet purge its corpus.
- The seat limit is `AURA_TEAM_DEFAULT_SEATS` (default 5) until plan entitlements replace it.
- Isolation is enforced in application code plus the scope string. **Postgres row-level security
  is the planned second lock** (spec §6.2), not built.
- Not built yet:
  - invites by email: the inviter currently receives the token to share;
  - shared team conversations;
  - client and console screens;
  - the sole-owner guard on account deletion (`TenancyService.sole_ownerships` exists for it).
- **Evidence so far:**
  - in-memory and real-Postgres contract tests;
  - HTTP isolation and IDOR tests.

  None of it has run in CI or been deployed. It stays "built, not proven" until it has.
