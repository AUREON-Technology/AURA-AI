# SaaS Phase 1 · Plan A — Workspaces foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every user has a personal workspace and can create Team workspaces with Owner/Admin/Member
roles and email-token invites. Shared knowledge in a team is scoped to that team and walled off from
every other workspace. `/metrics` fails closed in production.

**Architecture:** This plan adds a stdlib domain (`domain/tenancy.py`), a port (`ports/tenancy.py`),
an in-memory adapter and a Postgres adapter (Alembic `0020`), and an application service
(`tenancy/service.py`). The gateway's authenticator resolves an optional `X-Aura-Workspace` header
into a `WorkspaceContext` on `Principal`, checking membership in the store on every request. Routes
that read shareable data (knowledge, graph, chat retrieval) take their owner from
`Principal.knowledge_owner`, never from `caller.user_id`. Personal-life data (memory, Personal-OS,
feedback, runs, data keys) stays keyed to the user.

**Tech Stack:** Python 3.11, FastAPI, asyncpg, Alembic, pytest (in-memory substrate).

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` (§5.2, §6.1 items 3 and 8)

**Later plans (not this one):**
- B — sign-in providers + consumer web token + account linking;
- C — plans, entitlements and metering;
- D — billing (subscriptions, payment port, Fawry, IAP);
- E — desktop Engineer for customers (ADR-011 amendment);
- F — shared team conversations, client and console surfaces, transactional email, and the
  sole-owner guard on account deletion;
- G — GPU source and the live readiness items.

## Global Constraints

- Layering:
  - `domain/` and `ports/` are stdlib only.
  - Adapters import vendor SDKs lazily.
  - `container.py` is the only wiring point (`tests/architecture/`).
- Alembic is the only schema authority:
  - `0020` must have a working `downgrade()`;
  - no DDL under `aura_core` (`tests/architecture/test_no_runtime_ddl.py`).
- Every storage port keeps a real in-memory adapter, and the default `pytest` needs no Docker or
  network.
- Platform roles (`services/rbac.py`) and workspace roles never map onto each other. A workspace
  role grants no console route and no `admin.*` capability.
- Personal-life data never takes a workspace scope: memory, Personal-OS sync rows, feedback,
  agent runs, data keys.
- The personal scope equals the user's subject (`caller.user_id`) exactly, so existing data needs
  no rewrite.
- Team scope string: `workspace:<id>`. `workspace:` joins `RESERVED_PRINCIPAL_PREFIXES`, so no
  person's subject can take it.

**Departure from spec §5.1:** the active workspace travels in a request header, and membership is
checked against the store on each request, not baked into the JWT. That makes removal take effect
on the very next request (stronger than refresh-time re-checking) and leaves the v2 token format
untouched. The cost is one primary-key lookup per team-scoped request, the same pattern service
keys use.

**Invites:** no email sender exists yet (spec §8.4), so creating an invite returns its one-time
token to the inviting Owner/Admin to share. Plan F adds email delivery.

**Seats:** until Plan C's entitlements exist, a team's member limit comes from
`AURA_TEAM_DEFAULT_SEATS` (default `5`). Members plus pending invites may not exceed it.

## Review Focus

1. **A removed member keeps using a token they already hold.** The very next request with that
   workspace header must get 403, not team data.
2. **Header naming a workspace the caller never belonged to, or one that doesn't exist** must give
   the same 403. It must never be a 404 that reveals the workspace exists.
3. **The last Owner tries to leave, or is demoted or removed.** Refused: a team always has exactly
   one Owner.
4. **An invite token reused, expired, or used after the team filled up** must be refused, and the
   seat count is re-checked at accept time.
5. **A team member's chat turn** must retrieve team documents but never another member's private
   memory. The same person's turn in their personal workspace must not see team documents.

---

### Task 1: `/metrics` fails closed in production

**Files:**
- Modify: `aura_core/api/v3.py` (inside `build_app`, near the `MetricsAuthMiddleware` block, about
  line 1697)
- Modify: `aura_core/api/security_headers.py` (`MetricsAuthMiddleware`)
- Test: `tests/unit/test_metrics_fail_closed.py`

**Interfaces:**
- Produces: `MetricsAuthMiddleware(app, *, path: str, token: str | None)`. `token=None` means
  every request to `path` gets 404.

- [ ] **Step 1: Write the failing tests.**
  - `test_production_without_token_hides_metrics`: settings with `environment=production` and
    `metrics_token=""`; `GET /metrics` returns 404.
  - `test_production_with_token_requires_it`: the same but with a token; no auth gives 404, and
    `Authorization: Bearer <token>` gives 200.
  - `test_development_without_token_serves_metrics`: development and no token gives 200.

  Build settings the way `tests/unit/test_openapi_exposure.py` does.
- [ ] **Step 2:** Run `pytest tests/unit/test_metrics_fail_closed.py -v`. Expected: the first test
  FAILS (200).
- [ ] **Step 3: Implement.** In `build_app`, add the middleware when a token is set **or**
  `container.settings.environment.is_production_like`, passing `token or None`. In
  `MetricsAuthMiddleware._authorised`, a `None` token returns False. Update the class docstring:
  the VPS scrapes on the private network, so a production VPS that relies on unauthenticated
  scraping must set `AURA_METRICS_TOKEN` and configure Prometheus with it. Grep `deploy/` for the
  Prometheus scrape config and add `authorization: {credentials_file: ...}` or note it in the
  config's comment if a token file isn't available.
- [ ] **Step 4:** Run the new tests plus `pytest tests/unit -q -k "metrics or openapi or security_headers"`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `fix(security): /metrics fails closed in production without a token`.

### Task 2: Tenancy domain

**Files:**
- Create: `aura_core/domain/tenancy.py`
- Modify: `aura_core/domain/identifiers.py` (add `"workspace:"` to `RESERVED_PRINCIPAL_PREFIXES`)
- Test: `tests/unit/test_tenancy_domain.py`

**Interfaces:**
- Produces (all stdlib, frozen dataclasses with slots):
  - `class WorkspaceKind(str, Enum)`: `PERSONAL = "personal"`, `TEAM = "team"`.
  - `class WorkspaceRole(str, Enum)`: `OWNER = "owner"`, `ADMIN = "admin"`, `MEMBER = "member"`.
  - `Workspace(id: str, kind: WorkspaceKind, name: str, created_by: str, created_at: datetime)`.
  - `Membership(workspace_id: str, user_id: str, role: WorkspaceRole, joined_at: datetime, invited_by: str | None)`.
  - `Invite(invite_id: str, workspace_id: str, email: str, role: WorkspaceRole, digest: str, created_by: str, created_at: datetime, expires_at: datetime, accepted_at: datetime | None, accepted_by: str | None)`,
    with property `is_open(now) -> bool`.
  - `new_workspace_id() -> str`: 32 lowercase hex characters.
  - `workspace_scope(workspace_id: str) -> str` returns `f"workspace:{workspace_id}"`.
  - `new_invite_token() -> tuple[str, str]` returns `(invite_id, token)`. The token is
    `auraiv_<invite_id>_<secret>`, with `secrets.token_urlsafe(32)` as the secret.
  - `parse_invite_token(token) -> str` returns the invite_id, or raises `ValidationError`.
  - `invite_digest(token) -> str` is SHA-256 hex.
  - `can(role: WorkspaceRole, action: str, *, target_role: WorkspaceRole | None = None) -> bool`.
    The actions are `"view"`, `"invite"`, `"remove_member"`, `"change_role"`, `"transfer"`,
    `"delete"` and `"manage_knowledge"`:
    - Owner may do all of them.
    - Admin may `view`, `invite` (role Member or Admin), `manage_knowledge`, and `remove_member` /
      `change_role` only when `target_role == MEMBER`.
    - Member may `view` only.
  - Constants:
    - `INVITE_TTL = timedelta(days=7)`;
    - `MAX_WORKSPACE_NAME = 80`;
    - `clean_workspace_name(name) -> str`: NFKC, printable only, stripped, 1–80 characters, else
      `ValidationError`.

- [ ] **Step 1: Write the failing tests.**
  - `test_scope_is_reserved`: `is_reserved_principal(workspace_scope(new_workspace_id()))` is
    True.
  - `test_invite_token_roundtrip`: parse returns the id; the digest differs from the token; a
    malformed token raises.
  - `test_role_matrix`: table-driven over every (role, action, target_role) above, including
    "Admin cannot remove an Admin" and "Admin cannot transfer".
  - `test_invite_open`: an invite past `expires_at`, or one already accepted, is not open.
  - `test_workspace_name_rules`: an empty name, 81 characters, or control characters are refused.
- [ ] **Step 2:** Run `pytest tests/unit/test_tenancy_domain.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** `aura_core/domain/tenancy.py` and the prefix change.
- [ ] **Step 4:** Run the test, plus `pytest tests/architecture -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(tenancy): workspace domain, roles and invite tokens`.

### Task 3: Port and in-memory store

**Files:**
- Create: `aura_core/ports/tenancy.py`
- Create: `aura_core/adapters/tenancy.py`
- Test: `tests/unit/test_tenancy_store_contract.py`. Write it as a contract suite parametrised
  over store factories, so Task 4 adds the Postgres one.

**Interfaces:**
- Produces: `class TenancyStore(Protocol)` with these methods.
  - `async create_workspace(ws: Workspace, owner: Membership) -> None`: atomically. Raises
    `ConflictError` when `kind == PERSONAL` and `created_by` already has one.
  - `async get_workspace(workspace_id: str) -> Workspace | None`.
  - `async personal_workspace(user_id: str) -> Workspace | None`.
  - `async delete_workspace(workspace_id: str) -> None`: removes its members and invites.
  - `async memberships_of(user_id: str) -> list[tuple[Workspace, Membership]]`.
  - `async get_membership(workspace_id: str, user_id: str) -> Membership | None`.
  - `async list_members(workspace_id: str) -> list[Membership]`.
  - `async add_member(m: Membership) -> None`: raises `ConflictError` if the person is already a
    member.
  - `async set_role(workspace_id: str, user_id: str, role: WorkspaceRole) -> None`.
  - `async remove_member(workspace_id: str, user_id: str) -> bool`.
  - `async transfer_ownership(workspace_id: str, from_user: str, to_user: str) -> None`: one
    atomic step. The old Owner becomes Admin and the new one becomes Owner.
  - `async put_invite(i: Invite) -> None`.
  - `async get_invite(invite_id: str) -> Invite | None`.
  - `async list_invites(workspace_id: str) -> list[Invite]`: open and closed.
  - `async revoke_invite(invite_id: str) -> bool`.
  - `async accept_invite(invite_id: str, *, user_id: str, at: datetime, seat_limit: int) -> Membership`.
    Atomic. It raises `ConflictError` when the invite is not open, the person is already a member,
    or members ≥ `seat_limit`. On success it sets `accepted_at`/`accepted_by` and adds the
    membership with the invite's role.
- Produces: `InMemoryTenancyStore()`, guarded by one `asyncio.Lock`.

- [ ] **Step 1: Write the contract tests** (fixture `store` parametrised over `[InMemoryTenancyStore]`):
  - `test_one_personal_workspace_per_user`;
  - `test_memberships_of_lists_only_own`;
  - `test_accept_invite_adds_member_once`: the second accept raises `ConflictError`;
  - `test_accept_respects_seat_limit`;
  - `test_accept_expired_invite_refused`;
  - `test_transfer_swaps_roles`;
  - `test_delete_workspace_cascades`.
- [ ] **Step 2:** Run `pytest tests/unit/test_tenancy_store_contract.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the port and the in-memory adapter.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(tenancy): TenancyStore port and in-memory adapter`.

### Task 4: Migration `0020` and the Postgres store

**Files:**
- Create: `alembic/versions/0020_workspaces.py` (`revision="0020"`, `down_revision="0019"`;
  same shape as `0019_user_data_keys.py`)
- Create: `aura_core/adapters/postgres_tenancy.py` (shape of `postgres_service_keys.py`)
- Modify: `tests/unit/test_tenancy_store_contract.py` (add a Postgres factory, marked `load`, using
  `tests/postgres_migrations.py:migrated_database()`)
- Modify: whatever `tests/architecture/test_no_runtime_ddl.py` needs to recognise the new tables.
  Run it and follow its message.

**Tables:**
- `aura_workspaces`:
  - `id TEXT PK CHECK (id ~ '^[0-9a-f]{32}$')`;
  - `kind TEXT CHECK IN ('personal','team')`;
  - `name TEXT NOT NULL`;
  - `created_by TEXT NOT NULL REFERENCES users(auth_subject) ON DELETE CASCADE`;
  - `created_at TIMESTAMPTZ DEFAULT now()`;
  - partial unique index `(created_by) WHERE kind='personal'`.
- `aura_workspace_members`:
  - `workspace_id REFERENCES aura_workspaces ON DELETE CASCADE`;
  - `user_id TEXT REFERENCES users(auth_subject) ON DELETE CASCADE`;
  - `role CHECK IN ('owner','admin','member')`;
  - `invited_by TEXT`, `joined_at TIMESTAMPTZ`;
  - `PK (workspace_id, user_id)`;
  - index `(user_id)`;
  - partial unique index `(workspace_id) WHERE role='owner'`.
- `aura_workspace_invites`:
  - `invite_id TEXT PK`, `workspace_id` (FK, cascade), `email TEXT`, `role`, `digest TEXT`;
  - `created_by`, `created_at`, `expires_at`, `accepted_at`, `accepted_by`.
- `downgrade()` drops all three, in reverse order.

**Implementation notes:**
- `accept_invite` runs in one transaction:
  1. `SELECT … FOR UPDATE` on the invite;
  2. a member `count(*)` on the workspace;
  3. insert the member;
  4. update the invite.
- `transfer_ownership` runs in one transaction. Demote first, then promote, because of the
  one-owner partial index.

- [ ] **Step 1:** Add the Postgres factory to the contract suite.
- [ ] **Step 2:** If Docker or a local Postgres 16 is available, run
  `pytest tests/unit/test_tenancy_store_contract.py -m load -v`. Expected: FAIL (no table).
  Otherwise, install `postgresql-16` with apt and point `AURA_TEST_DATABASE_URL` (or whatever
  `tests/postgres_migrations.py` reads) at it. CLAUDE.md requires a real Postgres for a Postgres
  adapter.
- [ ] **Step 3: Implement** the migration and the adapter.
- [ ] **Step 4:** Run the contract suite with `-m load`, then
  `pytest tests/load/test_migration_roundtrip_postgres.py -m load -q`, then
  `pytest tests/architecture -q`. Expected: all PASS. The round-trip must show `0020` restoring
  `0019`'s schema exactly.
- [ ] **Step 5: Commit:** `feat(tenancy): migration 0020 and Postgres TenancyStore`.

### Task 5: `TenancyService` and container wiring

**Files:**
- Create: `aura_core/tenancy/__init__.py`, `aura_core/tenancy/service.py`
- Modify: `aura_core/config/settings.py`. Add `TenancySettings(default_team_seats: int = 5)`, read
  from `AURA_TEAM_DEFAULT_SEATS` (an integer from 2 to 1000, else a validation error), and hang it
  on `AuraSettings` as `tenancy`.
- Modify: `aura_core/container.py`. Add `self.tenancy = self._build_tenancy()`: Postgres when
  `document_backend == "postgres"`, in-memory otherwise, mirroring `_build_service_keys`.
- Test: `tests/unit/test_tenancy_service.py`

**Interfaces:**
- Consumes: Task 2 domain and Task 3 port; `container.clock`, `container.audit` (`AuditEvent`).
- Produces: `TenancyService(store, clock, audit, settings)` with these methods.
  - `async ensure_personal(user_id: str, display_name: str = "") -> Workspace`.
  - `async list_for(user_id: str) -> list[tuple[Workspace, Membership]]`: calls
    `ensure_personal` first.
  - `async resolve(user_id: str, workspace_id: str) -> tuple[Workspace, Membership]`: raises
    `AuthorizationError("not a member of this workspace")` for both "no such workspace" and "not
    a member".
  - `async create_team(user_id: str, name: str) -> Workspace`.
  - `async invite(actor: str, workspace_id: str, email: str, role: WorkspaceRole) -> tuple[Invite, str]`
    returns the token.
    - Refuses in a personal workspace and refuses `role=OWNER`.
    - Checks `can(...,"invite")`.
    - Checks seats: members + open invites < `default_team_seats`.
    - Lower-cases the email.
  - `async accept(user_id: str, token: str) -> Membership`: checks the digest with
    `hmac.compare_digest`.
  - `async revoke_invite(actor, workspace_id, invite_id) -> None`.
  - `async remove(actor, workspace_id, user_id) -> None`:
    - an Owner cannot be removed;
    - self-removal is `leave`;
    - checks `can(actor_role, "remove_member", target_role=...)`.
  - `async leave(user_id, workspace_id) -> None`: an Owner is refused with
    `ConflictError("transfer ownership first")`; a personal workspace is refused.
  - `async change_role(actor, workspace_id, user_id, role) -> None`: never to or from OWNER;
    checks `can`.
  - `async transfer(actor, workspace_id, to_user) -> None`: Owner only; the target must be a
    member.
  - `async delete_team(actor, workspace_id) -> None`: Owner only; never personal.
  - `async has_sole_ownership(user_id: str) -> list[Workspace]`: the team workspaces where the user
    is Owner. Plan F's account-deletion guard uses it.
- Every mutation writes one `AuditEvent`:
  - `action` is `"workspace.<verb>"`;
  - `actor=user_id`, `actor_role="workspace:<role>"`;
  - `resource=f"workspace:{id}"`;
  - `detail` never contains a token.

- [ ] **Step 1: Write the failing tests** (in-memory store, `clock` fixture):
  - `test_personal_created_once`;
  - `test_non_member_and_missing_workspace_same_error`;
  - `test_admin_can_invite_member_not_owner`;
  - `test_member_cannot_invite`;
  - `test_seat_limit_counts_open_invites`;
  - `test_accept_wrong_token_secret_refused`: right id, wrong secret;
  - `test_owner_cannot_leave_until_transfer`;
  - `test_admin_cannot_remove_admin`;
  - `test_personal_workspace_cannot_be_invited_into_or_deleted`;
  - `test_mutations_are_audited_without_tokens`.
- [ ] **Step 2:** Run `pytest tests/unit/test_tenancy_service.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the service, settings and wiring.
- [ ] **Step 4:** Run the test, plus `pytest tests/architecture -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(tenancy): TenancyService with role rules, seats and audit`.

### Task 6: Workspace on `Principal`, resolved by the authenticator

**Files:**
- Modify: `aura_core/api/v3.py` (class `Principal`)
- Modify: `aura_service.py` (`_build_authenticator`; pass `tenancy=container.tenancy` where it is
  called)
- Test: `tests/unit/test_workspace_principal.py`, using the
  `_a_super_admin_person`-style monkeypatch from `tests/unit/test_service_keys.py` but with role
  `"User"`

**Interfaces:**
- Produces:
  - `Principal.workspace: WorkspaceContext | None`, where
    `WorkspaceContext(workspace_id: str, kind: WorkspaceKind, role: WorkspaceRole)` is a frozen
    dataclass in `domain/tenancy.py`.
  - `Principal.knowledge_owner -> UserId`:
    - `UserId(workspace_scope(id))` for a TEAM workspace;
    - otherwise `self.user_id`.
- `_build_authenticator(settings, *, service_keys=None, tenancy=None)`:
  - After the JWT path, if the header `X-Aura-Workspace` is present and non-empty, call
    `tenancy.resolve(subject, value)`.
  - `AuthorizationError` becomes a 403 with the same body for "unknown" and "not a member".
  - With the header absent, `workspace=None` and there is no store call.
  - Service-key principals that send the header are refused with 403.
  - The workspace role adds nothing to `capabilities`.

- [ ] **Step 1: Write the failing tests.**
  - `test_no_header_is_personal_scope_without_store_call`: use a store spy that counts calls.
  - `test_team_header_sets_knowledge_owner`.
  - `test_removed_member_403_on_next_request`: resolve, remove, then the same token is refused.
  - `test_unknown_and_foreign_workspace_identical_403`: compare the response bodies.
  - `test_workspace_owner_gets_no_admin_capability`: `Principal.capabilities` has no `admin.*`,
    and `GET /api/v3/admin/...` (any admin route) is still 403.
  - `test_service_key_with_workspace_header_refused`.
- [ ] **Step 2:** Run `pytest tests/unit/test_workspace_principal.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the test, plus `pytest tests/unit -q -k "authenticator or service_keys or principal"`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `feat(tenancy): resolve X-Aura-Workspace into Principal per request`.

### Task 7: Scope shareable data by `knowledge_owner`

**Files:**
- Modify: `aura_core/api/v3.py`. Every `owner_id=caller.user_id` passed to `container.knowledge.*`,
  `container.graph.*` or `container.cag.*` becomes `owner_id=caller.knowledge_owner`. That covers
  the ingest, upload, search, document list, delete, pin and graph routes (lines around 757–1052).
  Mutating knowledge routes in a team additionally require `can(role, "manage_knowledge")`; a
  Member gets 403.
- Modify: `aura_core/kernel/kernel.py`:
  - `TurnRequest` gains `knowledge_owner: UserId | None = None`.
  - The `retrieve()` closure uses `request.knowledge_owner or request.user_id`.
  - Memory recall keeps `request.user_id`.
- Modify: `aura_core/api/v3.py` (the `TurnRequest(...)` build near line 459) to pass
  `knowledge_owner=caller.knowledge_owner`.
- Check: any `delegate_to_agent` child `TurnRequest` construction (`aura_core/tools/delegation.py`)
  copies `knowledge_owner` from its parent.
- Create: `tests/architecture/test_knowledge_scope_from_principal.py`. It parses `api/v3.py` with
  `ast` and fails if any call whose receiver chain contains `knowledge`, `graph` or `cag` passes
  `owner_id=caller.user_id`.
- Test: `tests/integration/test_workspace_isolation.py`

- [ ] **Step 1: Write the failing tests.**
  - The architecture test: it fails on the current `v3.py`.
  - Isolation tests, run through the HTTP app with two people A and B, team T1 (A owner, B member)
    and team T2 (B only):
    - `test_team_doc_visible_to_team_member`: A ingests with header T1; B searches with T1 and
      finds it.
    - `test_team_doc_invisible_in_personal_and_other_team`: B with no header, and B with T2, do
      not find it.
    - `test_personal_doc_invisible_in_team`.
    - `test_member_cannot_ingest_into_team`: 403.
    - `test_chat_turn_retrieves_team_scope_but_user_memory`: use the `echo` provider. A turn with
      header T1 has retrieval owner `workspace:T1` and memory owner B. Assert this through the
      knowledge/memory in-memory stores' recorded queries, or through the turn's citations.
    - `test_removed_member_loses_team_docs_immediately`.
- [ ] **Step 2:** Run `pytest tests/architecture/test_knowledge_scope_from_principal.py tests/integration/test_workspace_isolation.py -v`.
  Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run those tests, then the whole `pytest -q` and
  `TESTING=True python -m unittest discover -p "test_*.py"`. Expected: all PASS. Quote the counts.
- [ ] **Step 5: Commit:** `feat(tenancy): shareable knowledge scoped to the active workspace`.

### Task 8: Workspace HTTP routes

**Files:**
- Create: `aura_core/api/workspaces.py` with
  `build_workspaces_router(container, *, principal) -> APIRouter`. It is mounted from
  `build_router` in `v3.py` the way `admin_service_keys` is.
- Test: `tests/integration/test_workspaces_api.py`

**Routes** (all under `/api/v3`):
- They require a person principal: a service key gets 403.
- Mutations take `dependencies=idempotent`.
- Errors use the existing `AuraError.to_dict()` mapping.

| Method | Path | Body → result |
|---|---|---|
| GET | `/workspaces` | → `{"workspaces": [{"id","kind","name","role"}]}` (ensures personal) |
| POST | `/workspaces` | `{"name"}` → 201 `{"id","kind":"team","name","role":"owner"}` |
| DELETE | `/workspaces/{id}` | Owner, team only → 204 |
| GET | `/workspaces/{id}/members` | member → `{"members":[{"user_id","role","joined_at"}]}` |
| PATCH | `/workspaces/{id}/members/{user_id}` | `{"role":"admin"\|"member"}` → 204 |
| DELETE | `/workspaces/{id}/members/{user_id}` | → 204 (self = leave) |
| POST | `/workspaces/{id}/transfer` | `{"user_id"}` → 204 |
| POST | `/workspaces/{id}/invites` | `{"email","role"}` → 201 `{"invite":{…no digest…},"token"}` |
| GET | `/workspaces/{id}/invites` | Owner/Admin → open invites, without digests |
| DELETE | `/workspaces/{id}/invites/{invite_id}` | → 204 |
| POST | `/workspaces/invites/accept` | `{"token"}` → 200 `{"workspace":{…},"role"}` |

- Rate limiting: invite creation and accept use the existing `enforce_rate_limit` with a new
  `ROUTE_COSTS` class `"workspace_invite"`, cost 5.
- Note on `GET /workspaces/{id}/members`: it returns user ids only (no emails) in this plan. Plan F
  adds display names via the users table.

- [ ] **Step 1: Write the failing tests** (a happy path per row, plus):
  - `test_invite_response_never_contains_digest`;
  - `test_accept_rate_limited`;
  - `test_service_key_refused_on_workspace_routes`;
  - `test_member_cannot_list_invites`.
- [ ] **Step 2:** Run `pytest tests/integration/test_workspaces_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run it, plus the full `pytest -q` and the legacy `unittest` suite. Expected: PASS
  (quote the counts).
- [ ] **Step 5: Commit:** `feat(tenancy): /api/v3/workspaces routes`.

### Task 9: Docs and status

**Files:**
- Create: `docs/architecture/034-workspaces-and-tenancy.md` (ADR, Proposed). Cover:
  - why the scope reuses `owner_id`;
  - the header-plus-lookup choice (the departure from spec §5.1);
  - what stays personal;
  - the RLS follow-up.
- Modify: `docs/status/BACKEND_STATUS.md`. Add a "Workspaces (SaaS P1-A)" row: BUILT, tested
  locally, not CI-run, not deployed.
- Modify: `CLAUDE.md`. Update the ADR range ("`001-*.md`–`034-*.md`") and add a one-line pointer
  for workspaces (034).

- [ ] **Step 1:** Write the ADR and the status row.
- [ ] **Step 2:** Run `pytest tests/unit -q -k "docs or status or claude_md"`, if any doc-pinning
  tests exist. Expected: PASS.
- [ ] **Step 3: Commit:** `docs: ADR-034 workspaces and tenancy; status row`.
