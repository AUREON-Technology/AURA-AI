# SaaS Phase 1 · Plan C — Plans, entitlements and metering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each workspace has exactly one plan (Free, Pro or Team). The server enforces that plan's
limits:
- chat messages per month;
- the Engineer agent;
- knowledge storage;
- team seats.

An operator can set a workspace's plan by hand until billing (Plan D) does it. A customer who hits
a limit gets a typed refusal the app turns into a plain sentence.

**Architecture:**
- **Plans.** A stdlib plan catalogue (`domain/plans.py`), as data with built-in placeholder
  numbers. `AURA_PLAN_CATALOGUE` (JSON) replaces it without a code change.
- **Storage ports.** Two new ports, each with an in-memory and a Postgres adapter (Alembic
  `0021`):
  - `EntitlementStore`: which plan and how many seats a workspace has;
  - `UsageMeter`: atomic per-workspace counters with a limit check in the same statement.
- **Service.** `PlanService` (`aura_core/plans/`) decides. The gateway asks it before a chat turn
  or an ingest, and gives the unit back when the turn fails. `TenancyService` takes its seat limit
  from it.

**Tech Stack:** Python 3.11, FastAPI, asyncpg, Alembic; Flutter for the one client message.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.3 (plans; limits as data;
metering; limit responses; plans follow the workspace), §5.4 (the operator's manual plan action).

## Global Constraints

- **Plans follow the workspace** (owner decision). The metered workspace is
  `caller.workspace.workspace_id`, or the caller's personal workspace
  (`TenancyService.ensure_personal`) when no header is sent.
- **Limits are data.** No code compares a plan *name*; everything reads `PlanLimits`.
- **Placeholder numbers** until the owner sets them (spec §8.1):
  - free: 50 messages/month, no Engineer, 25 MiB storage, max 3 seats;
  - pro: 1500 messages/month, Engineer, 1 GiB, 1 seat (personal workspaces only);
  - team: 1500 messages per seat per month pooled, Engineer, 5 GiB, 2–50 seats (team workspaces
    only).
- **Period.** The UTC calendar month `YYYY-MM`. Plan D re-keys it to the billing period. Storage
  is a running total with period `all`.
- **Failed turns are not counted.** A unit is reserved before the kernel runs and released when
  the turn raises, or when a stream ends with an `error` frame and no `done`.
- **Operators are never metered.** A platform role at or above `CONSOLE_MIN_ROLE` (Developer) is
  exempt, and so is any service-key principal (ADR-029 has its own budget). Both are recorded as
  unmetered, not refused.
- **The refusal** is `PlanLimitError(AuraError)`: `kind="plan_limit"`, HTTP **402**, `details`
  `{"limit": "messages"|"engineer"|"storage"|"seats", "plan": id, "used": n, "allowed": n, "resets_at": iso|None}`.
- Alembic is the only schema authority, with a working `downgrade()`. Every port has an in-memory
  adapter. The default `pytest` needs no network.

## Review Focus

1. **Two concurrent turns on the last message.** Exactly one is admitted. The meter's check and
   increment are one atomic step, in Postgres too.
2. **A turn that fails after admission** (provider down, `AllProvidersFailedError`) does not use
   up the month. Neither does a client that disconnects mid-stream after a `done`... unless no
   `done` was sent.
3. **A member removed mid-month, or a plan downgraded mid-month.** Usage stays with the workspace,
   and the new, lower limit applies on the next request. A count already above the new limit
   refuses; it is never negative.
4. **Team seats.** An invite that would exceed the plan's seats is refused at invite and at
   accept. A downgrade below the current member count keeps the existing members but refuses new
   ones.
5. **Storage.** Re-ingesting identical content (`created=False`) does not count twice. Deleting a
   document gives its bytes back, and the total never goes below 0.

---

### Task 1: Plan domain and catalogue

**Files:**
- Create: `aura_core/domain/plans.py`
- Modify: `aura_core/domain/errors.py` (add `PlanLimitError`)
- Modify: `aura_core/config/settings.py`. `TenancySettings` gains
  `plan_catalogue_json: str = ""` from `AURA_PLAN_CATALOGUE`.
- Test: `tests/unit/test_plans_domain.py`

**Interfaces (stdlib):**
- `PlanLimits(id: str, messages_per_month: int, messages_pooled_per_seat: bool, engineer: bool, storage_bytes: int, min_seats: int, max_seats: int, workspace_kinds: frozenset[WorkspaceKind])`,
  a frozen dataclass.
- `DEFAULT_CATALOGUE: Mapping[str, PlanLimits]` (the placeholder numbers above) and
  `DEFAULT_PLAN = "free"`.
- `load_catalogue(raw_json: str) -> Mapping[str, PlanLimits]`:
  - an empty string gives the default;
  - otherwise it validates every field (non-negative integers, `1 <= min_seats <= max_seats`,
    known kinds, `free` present) and raises `ConfigurationError`.
- `Entitlement(workspace_id: str, plan_id: str, seats: int, source: str, updated_at: datetime, updated_by: str)`.
  `source` is `"manual"` or `"subscription"`.
- `monthly_message_limit(plan: PlanLimits, seats: int) -> int`. It returns the limit × seats when
  pooled, else the limit.
- `period_for(at: datetime) -> str` returns `"YYYY-MM"` (UTC).
- `period_resets_at(period: str) -> datetime` returns the first instant of the next month (UTC).
- `PlanLimitError(message, *, limit, plan, used, allowed, resets_at=None)` with
  `kind="plan_limit"`, `http_status=402` and the details listed above.

- [ ] **Step 1: Write the failing tests.**
  - `test_default_catalogue_has_three_plans`;
  - `test_pooled_team_limit_scales_with_seats`;
  - `test_load_catalogue_validates`: missing `free`, negative numbers and `min_seats >
    max_seats` all raise; valid JSON round-trips;
  - `test_period_and_reset_across_year_end` (`2026-12-31T23:59Z` → `"2026-12"`, resets
    `2027-01-01`);
  - `test_plan_limit_error_shape` (402, kind, details).
- [ ] **Step 2:** Run `pytest tests/unit/test_plans_domain.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run it, plus `pytest tests/architecture -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(plans): plan catalogue as data, entitlement and limit error`.

### Task 2: Ports, in-memory adapters, migration 0021, Postgres adapters

**Files:**
- Create: `aura_core/ports/plans.py`, `aura_core/adapters/plans.py`,
  `aura_core/adapters/postgres_plans.py`
- Create: `alembic/versions/0021_plans_and_usage.py`
- Test: `tests/unit/test_plan_stores_contract.py` (in-memory by default; Postgres marked `load`)
- Modify: the `0020`/`0021` head pins (`tests/unit/test_schema_readiness.py` → `0021`).

**Interfaces:**
- `EntitlementStore` with:
  - `get(workspace_id) -> Entitlement | None`;
  - `put(e: Entitlement) -> None` (upsert).
- `UsageMeter` with:
  - `try_consume(workspace_id, metric, period, amount, limit) -> tuple[bool, int]`. Atomic: add
    `amount` only if `used + amount <= limit`. It returns `(admitted, used_after_or_current)`;
  - `release(workspace_id, metric, period, amount) -> int`, floored at 0;
  - `add(workspace_id, metric, period, amount) -> int`, unconditional (storage), floored at 0;
  - `get(workspace_id, metric, period) -> int`.

**Tables:**
- `aura_workspace_entitlements`:
  - `workspace_id TEXT PK REFERENCES aura_workspaces(id) ON DELETE CASCADE`;
  - `plan_id TEXT NOT NULL`;
  - `seats INT NOT NULL CHECK (seats >= 1)`;
  - `source TEXT CHECK IN ('manual','subscription')`;
  - `updated_at TIMESTAMPTZ`, `updated_by TEXT`.
- `aura_usage_counters`:
  - `workspace_id TEXT NOT NULL REFERENCES aura_workspaces(id) ON DELETE CASCADE`;
  - `metric TEXT NOT NULL`, `period TEXT NOT NULL`;
  - `value BIGINT NOT NULL CHECK (value >= 0)`;
  - `PK (workspace_id, metric, period)`.

**Postgres `try_consume`** is one statement:

```sql
INSERT … VALUES ($1,$2,$3,$4) ON CONFLICT (…) DO UPDATE SET value = c.value + $4
WHERE c.value + $4 <= $5 RETURNING value
```

with the first insert guarded by `$4 <= $5`. When no row is returned, select the current value and
return `(False, current)`.

- [ ] **Step 1: Write the contract tests** (`store` and `meter` fixtures parametrised over
  in-memory and Postgres):
  - `test_entitlement_upsert`;
  - `test_try_consume_admits_until_limit`;
  - `test_try_consume_concurrent_last_unit`: 20 concurrent `try_consume(…,1, limit=10)` admit
    exactly 10;
  - `test_release_never_negative`;
  - `test_add_and_floor`;
  - `test_periods_are_independent`;
  - Postgres only: `test_workspace_delete_cascades`.
- [ ] **Step 2:** Run with `-m "load or not load"` and `AURA_TEST_DATABASE_URL` set. Expected:
  FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the contract tests, `tests/load/test_migration_roundtrip_postgres.py -m load`,
  `tests/architecture` and `tests/unit/test_schema_readiness.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(plans): entitlement store and usage meter (migration 0021)`.

### Task 3: `PlanService`, and seats from the plan

**Files:**
- Create: `aura_core/plans/__init__.py`, `aura_core/plans/service.py`
- Modify: `aura_core/tenancy/service.py`. The seat limit comes from an injected
  `seat_limit: Callable[[str], Awaitable[int]]`. When none is injected,
  `settings.default_team_seats` keeps today's behaviour for tests.
- Modify: `aura_core/container.py` (wire `self.plans`, and give tenancy `plans.seat_limit`).
- Test: `tests/unit/test_plan_service.py`

**Interfaces:**
- `PlanService(entitlements, meter, tenancy, clock, audit, catalogue)` with:
  - `async entitlement(workspace_id) -> tuple[PlanLimits, int]` (plan and seats; default `free`
    with seats 1);
  - `async seat_limit(workspace_id) -> int`. It returns `max(seats, plan.min_seats)` capped at
    `plan.max_seats`; for `free` it is `plan.max_seats`;
  - `async metered_workspace(caller) -> str | None`. `None` means unmetered: operators, services,
    and `allow_mock_auth`;
  - `async admit_message(workspace_id) -> Reservation`. It raises `PlanLimitError(limit="messages")`;
  - `async release(reservation) -> None`, idempotent;
  - `async require_engineer(workspace_id) -> None`, raising `PlanLimitError(limit="engineer")`;
  - `async admit_storage(workspace_id, nbytes) -> None`, raising `PlanLimitError(limit="storage")`;
  - `async free_storage(workspace_id, nbytes) -> None`;
  - `async usage(workspace_id) -> dict` with plan, seats, limits, `messages_used`,
    `storage_used`, `period` and `resets_at`;
  - `async set_plan(actor, workspace_id, plan_id, seats) -> Entitlement`. It refuses a plan whose
    `workspace_kinds` excludes the workspace's kind, and seats outside `[min,max]`. It writes
    `source="manual"` and audits `plan.set`.
- `Reservation(workspace_id, metric, period, amount)` is a frozen dataclass with a `released` flag
  kept by the service.

- [ ] **Step 1: Write the failing tests:**
  - `test_free_by_default`;
  - `test_messages_refused_at_limit_with_reset_time`;
  - `test_release_returns_the_unit`;
  - `test_engineer_refused_on_free_allowed_on_pro`;
  - `test_storage_admit_and_free`;
  - `test_set_plan_validates_kind_and_seats_and_audits`;
  - `test_downgrade_applies_next_request_and_never_negative`;
  - `test_team_seats_follow_plan` (TenancyService invite/accept use `plans.seat_limit`);
  - `test_operator_and_service_unmetered`.
- [ ] **Step 2:** Run `pytest tests/unit/test_plan_service.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run it, plus `tests/unit/test_tenancy_service.py` and `tests/architecture`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `feat(plans): PlanService; team seats from the plan`.

### Task 4: Enforcement in the gateway

**Files:**
- Modify: `aura_core/api/v3.py`:
  - **`/chat`:** `admit_message` before `kernel.run`; release on exception.
  - **`/chat/stream`:** admit before streaming; release if the generator ends without a `done`
    frame (an exception, an `error` frame, or a disconnect before `done`).
  - **Engineer turns:** a turn whose `payload.agent == "engineer"` calls `require_engineer` first.
  - **Ingest and upload:** `admit_storage(len(content.encode()))` before ingest; on
    `created=False` (duplicate) the bytes are given back.
  - **Delete:** frees the document's stored content length, read through
    `container.documents.get(...)` before deletion, scoped by `knowledge_owner`.
- Test: `tests/integration/test_plan_enforcement.py` (real app, real authenticator stub as in
  `tests/integration/test_workspaces_api.py`, echo provider)

- [ ] **Step 1: Write the failing tests:**
  - `test_free_workspace_refused_after_limit_402_body`, with the catalogue overridden to 2
    messages through `AURA_PLAN_CATALOGUE`;
  - `test_failed_turn_not_counted`: monkeypatch `container.kernel.run` to raise
    `AllProvidersFailedError`;
  - `test_stream_counts_once_and_releases_on_error`;
  - `test_team_usage_pooled_across_members`;
  - `test_personal_and_team_metered_separately`;
  - `test_engineer_turn_refused_on_free`;
  - `test_ingest_storage_limit_and_duplicate_not_double_counted`;
  - `test_delete_frees_storage`;
  - `test_operator_unmetered`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the full `pytest -q` and the legacy `unittest`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(plans): enforce messages, Engineer and storage limits in the gateway`.

### Task 5: Plan routes

**Files:**
- Modify: `aura_core/api/workspaces.py` (add `GET /workspaces/{id}/plan`; any member)
- Create: `aura_core/api/admin_plans.py` (`PUT /admin/workspaces/{id}/plan` with
  `{"plan","seats"}`; SuperAdmin; idempotency-keyed; mounted like `admin_service_keys`)
- Modify: `tests/security/test_idor.py` (`COVERED`), `scripts/api_reachability.py` (`TRIAGE`) and
  the regenerated matrix
- Test: `tests/integration/test_plan_routes.py`

- [ ] **Step 1: Write the failing tests:**
  - `test_member_reads_plan_and_usage`;
  - `test_stranger_403`;
  - `test_superadmin_sets_plan`;
  - `test_non_superadmin_403`;
  - `test_set_plan_rejects_wrong_kind_422`.
- [ ] **Step 2–4:** Implement and run them, plus `tests/security`, `tests/architecture`. Expected:
  PASS.
- [ ] **Step 5: Commit:** `feat(plans): plan and usage route; operator sets a workspace plan`.

### Task 6: Client wording for plan limits

**Files:**
- Modify: `aura_client/lib/core/network/aura_failure.dart` (map HTTP 402 / `error: "plan_limit"`
  to a new `AuraFailureKind.planLimit`, with a title and message per `details.limit`)
- Modify: `aura_client/lib/l10n/app_en.arb` (strings), then `flutter gen-l10n`
- Test: `aura_client/test/plan_limit_failure_test.dart`

- [ ] **Step 1: Write the failing tests:**
  - messages: "You've used this month's messages"; the message names the reset date and says
    upgrading raises the limit;
  - engineer: "The Engineer agent is on Pro and Team plans";
  - storage;
  - seats;
  - `canRetry` is false.
- [ ] **Step 2–4:** Implement, then run `flutter analyze` and the full `flutter test`. Expected:
  PASS (quote the counts).
- [ ] **Step 5: Commit:** `feat(client): explain plan limits`.

### Task 7: Docs

**Files:**
- Modify: `docs/architecture/034-workspaces-and-tenancy.md` (a section "Plans and metering")
- Modify: `docs/status/BACKEND_STATUS.md` (row)
- Create: `docs/deployment/plans-and-limits.md` (how to change the catalogue with
  `AURA_PLAN_CATALOGUE`; the operator `PUT`; what each refusal looks like)

- [ ] **Step 1:** Write them. **Step 2:** Run `pytest tests/architecture -q`. **Step 3: Commit:**
  `docs: plans and limits`.
