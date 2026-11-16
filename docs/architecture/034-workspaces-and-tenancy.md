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

## Consequences

- Tables `aura_workspaces`, `aura_workspace_members` and `aura_workspace_invites` arrive with Alembic
  `0020`. Partial unique indexes enforce one personal workspace per person and one owner per team,
  even under a race the code missed.
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
