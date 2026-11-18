# SaaS Phase 1 · Plan E — Desktop Engineer for customers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A Pro or Team customer can sign in to the desktop app, pair `aura_hostd`, and run the
Engineer agent on their own computer. A Free or downgraded workspace cannot. No customer desktop
session ever reaches a console route.

**Architecture:**
- **One switch.** `AURA_ENGINEER_FOR_CUSTOMERS`, default off. With it off, every route behaves
  exactly as today (operators only, F-09).
- **The gate moves from role to entitlement.** `/desktop/login`, pairing approval and desktop
  refresh accept an operator as today, *or* a person with at least one workspace whose plan has
  `engineer`.
- **The role is capped.** A customer desktop session carries `channel=consumer_desktop`, which
  `rbac.get_user_role` caps at `User` on every path, the same mechanism `consumer_web` uses
  (Plan B). An unattested desktop session is therefore never worth more than a browser session.
- **Turns.** For a customer, `admit_turn` adds the `engineer_agent` capability when the active
  workspace's plan has Engineer, and still strips it when it does not.
- **The daemon socket** (`/host/agent`) accepts an entitled customer and re-checks entitlement on
  its existing 60-second recheck. A downgrade closes the socket, refuses the next desktop refresh,
  and strips Engineer from the next turn.
- **Startup refuses** the switch alongside anything a customer must never reach: the server
  sandbox (phase 1b), the shared admin GitHub token, operator-configured MCP servers, or relaxed
  confirmation settings.

**Tech stack:** FastAPI, existing `services/` auth (legacy tree, `api/v2/auth.py`), `aura_core`
plans and tenancy (Plans A and C).

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.1 ("Desktop Engineer for
customers"), §6.1 item 10, §7 step 7.

## Global Constraints

- "`/desktop/login`, desktop pairing and their refresh path stop requiring a console role. They
  require instead a workspace whose entitlement includes Engineer."
- The spec requires four things:
  - an ADR-011 threat-model update;
  - per-user pairing and rate limits;
  - the existing per-call confirmation for side-effecting host tools (ADR-006), kept mandatory;
  - revocation on plan downgrade.
- "It still must never grant console access."
- Server sandbox workspaces for customers are phase 1b: "Railway must keep
  `AURA_SANDBOX_ENABLED=false`."
- Off by default. Both suites (`pytest`, legacy `unittest`) stay green with the switch off and
  unchanged.
- Legacy routes reach the container through `request.app.state.aura` (as `api/automation.py`
  does). With no container there, a person counts as not entitled.

## Review Focus

1. **An operator's email on a customer desktop session.** Even with the email in
   `SUPERADMIN_EMAILS`, the role stays `User` on issue, on refresh, and in the gateway. Test in
   Task 3.
2. **A downgrade mid-session.** The daemon socket closes at its next recheck, the next desktop
   refresh is 401, and the next turn has no `engineer_agent`. Tests in Tasks 2 and 3.
3. **A team member removed from the team** (their only Engineer workspace) is no longer entitled.
   Test in Task 2.
4. **With the switch off,** a customer gets today's 403 on login, approval and refresh. The
   existing `test_desktop_login.py` and `test_device_pairing.py` pass unchanged. Task 3.
5. **An operator's desktop session** keeps its operator role and no channel cap. Test in Task 3.

---

### Task 1: Switch, startup guards, the `consumer_desktop` cap

**Files:**
- Modify: `aura_backend/aura_core/config/settings.py` (`SecuritySettings`, `AuraSettings.validate`)
- Modify: `aura_backend/services/rbac.py`
- Test: `aura_backend/tests/unit/test_engineer_for_customers_config.py`

**Interfaces:**
- Produces:
  - `SecuritySettings.engineer_for_customers: bool = False`, read from
    `AURA_ENGINEER_FOR_CUSTOMERS`.
  - `services.rbac.CONSUMER_DESKTOP_CHANNEL = "consumer_desktop"`.
  - `services.rbac.CONSUMER_CHANNELS = frozenset({CONSUMER_WEB_CHANNEL, CONSUMER_DESKTOP_CHANNEL})`.

- [ ] **Step 1: Failing tests.**
  - `test_switch_defaults_off`.
  - `test_switch_refuses_server_sandbox`: `validate()` raises with `sandbox.enabled=True`.
  - `test_switch_refuses_shared_github_token`: raises with `github.enabled=True` and
    `oauth_enabled=False`; passes with `oauth_enabled=True`.
  - `test_switch_refuses_mcp`: raises with `mcp.enabled=True`.
  - `test_switch_refuses_relaxed_confirmation`: raises with
    `tools.require_confirmation_for_side_effects=False` or
    `tools.block_side_effects_when_tainted=False`.
  - `test_consumer_desktop_is_capped_at_user`:
    `get_user_role({"uid": "u", "email": "boss@example.com", "email_verified": True, "sign_in_provider": "google.com", "channel": "consumer_desktop"})`
    returns `"User"` with `SUPERADMIN_EMAILS=boss@example.com`.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3: Implement.** Make each `validate()` problem a single sentence naming the variable
  to change. `get_user_role` tests `user_info.get("channel") in CONSUMER_CHANNELS`.
- [ ] **Step 4:** Run the tests, plus `tests/unit/test_consumer_web_sign_in.py`.
- [ ] **Step 5:** Commit: `feat(engineer): customer switch, startup guards, consumer_desktop role cap`.

### Task 2: Entitlement, turn capability, daemon socket

**Files:**
- Modify: `aura_backend/aura_core/plans/service.py`
- Modify: `aura_backend/aura_core/api/v3.py` (`admit_turn`)
- Modify: `aura_backend/aura_core/api/host_agent.py`
- Modify: `aura_backend/aura_core/api/ws_auth.py` (`LiveCredential.still_valid`)
- Test: `aura_backend/tests/unit/test_engineer_entitlement.py`,
  `aura_backend/tests/integration/test_host_agent_customers.py`

**Interfaces:**
- Produces:
  - `PlanService.engineer_entitled(subject: str) -> bool` (async). It is true when any workspace
    the subject belongs to (`tenancy.list_for`) has an entitlement whose plan has `engineer`.
  - `LiveCredential.still_valid(accept)`, where `accept` may return a bool or an awaitable bool.
- Consumes: `SecuritySettings.engineer_for_customers` (Task 1).

- [ ] **Step 1: Failing tests.**
  - `test_entitled_by_personal_pro_or_team_membership`: a Free personal workspace gives False.
    Setting it to Pro gives True. A Free user who joins a Team workspace gives True; removing
    them gives False.
  - `test_admit_turn_grants_engineer_to_a_pro_customer_only_when_switched_on`: a User-role caller
    on a Pro personal workspace gets `engineer_agent` on the admitted turn with the switch on, and
    not with it off. On Free the turn never has it.
  - `test_customer_daemon_connects_when_entitled`.
  - `test_customer_daemon_refused_on_free` (close code 4401).
  - `test_downgrade_closes_the_daemon_socket_at_recheck`: run with `recheck_s` small and the
    plan set back to Free; the socket is closed with 4401.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3: Implement.**
  - `admit_turn`: when `plan.engineer`, the switch is on and the turn lacks the capability, add
    `"engineer_agent"`.
  - `_may_host(principal)` becomes async. It accepts the role capability, or the switch plus
    `container.plans.engineer_entitled(principal.user_id.value)`.
- [ ] **Step 4:** Run the new tests plus `tests/unit/test_host_agent.py` and
  `tests/integration/test_plans_*`.
- [ ] **Step 5:** Commit: `feat(engineer): Engineer for entitled customers on turns and the host socket`.

### Task 3: Desktop sign-in, refresh and pairing for customers

**Files:**
- Modify: `aura_backend/api/v2/auth.py`
- Test: `aura_backend/tests/integration/test_desktop_engineer_customers.py`

**Interfaces:**
- Consumes:
  - `CONSUMER_DESKTOP_CHANNEL` (Task 1).
  - `PlanService.engineer_entitled` (Task 2), reached through `request.app.state.aura`.
- Produces, in `api/v2/auth.py`:
  - `async _engineer_entitled(request, uid: str) -> bool`.
  - `async _desktop_gate(request, user_info: dict, *, what: str) -> None`. An operator passes
    unchanged. An entitled customer gets `user_info["channel"] = CONSUMER_DESKTOP_CHANNEL` and a
    re-stamped role. Anyone else gets 403, with a detail naming the Pro or Team plan when the
    switch is on, and today's operator message when it is off.
  - `MAX_DESKTOP_DEVICES_PER_USER = 10`, `PAIR_APPROVALS_PER_HOUR = 5`,
    `DESKTOP_SIGN_INS_PER_MINUTE = 10` (per client address, the same trusted-address keying as
    `_consumer_rate_limit`).

- [ ] **Step 1: Failing tests.** Use a FastAPI app with `router` and `app.state.aura = container`.
  Verification is patched to return the person, as in `test_consumer_web_sign_in.py`.
  - `test_pro_customer_desktop_login_is_capped_and_device_bound`: 200. The token has
    `channel=consumer_desktop`, `role=User` and a `device_id`, even with the email in
    `SUPERADMIN_EMAILS`.
  - `test_free_customer_cannot_sign_in_pair_or_refresh`:
    - login → 403;
    - approve → 403;
    - a customer desktop refresh token after a downgrade → 401.
  - `test_switch_off_keeps_operators_only`: a Pro customer gets 403 on login and approve.
  - `test_operator_desktop_session_unchanged`: no channel, role SuperAdmin.
  - `test_customer_desktop_session_never_reaches_the_console`: a customer desktop access token,
    sent through `build_app`'s real authenticator, gets 403 on `GET /api/v3/admin/status` and on
    one `admin_platform` route.
  - `test_pairing_limits`: the 6th approval in an hour → 429. Approval with 10 live devices
    → 409.
  - `test_daemon_token_from_a_customer_approval_is_channel_capped`.
  - `test_desktop_sign_in_rate_limited` → 429 after 10 in a minute.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3: Implement.**
  - `/desktop/login`: rate limit, then `_desktop_gate`.
  - `/refresh` gains `http_request: Request`. For a device session, an operator passes; a
    `consumer_desktop` identity passes when entitled; anyone else gets 401.
  - `/desktop/pair/approve`: `_desktop_gate` on the approver, then the two limits.
  - `/desktop/pair/poll`: the token carries the approver's channel when they were not an
    operator.
  - `/desktop/pair/start`: the per-address rate limit.
- [ ] **Step 4:** Run the new tests, plus `python -m unittest test_desktop_login test_device_pairing test_web_token`
  (legacy, unchanged) and `tests/security`.
- [ ] **Step 5:** Commit: `feat(engineer): desktop sign-in and pairing for entitled customers`.

### Task 4: ADR-011 amendment, docs

- **ADR-011**, a new section "Customers (SaaS P1-E): threat model". It covers:
  - who can now hold a device session;
  - why the `User` cap makes it no stronger than a browser session (the attestation gap of F-09
    stops mattering);
  - what is mandatory and why the startup guards exist: confirmation, taint, the daemon's own
    folder trust, no shared credentials;
  - revocation on downgrade: refresh, socket recheck, turn;
  - what stays out: the server sandbox, MCP and the shared GitHub token.
- **`docs/deployment/desktop-engineer-customers.md`**: the switch, its guards, what customers
  need (Pro or Team, the desktop app, `aura_hostd`), and how to verify.
- **Status:** a row in `docs/status/BACKEND_STATUS.md`, and spec §6.1 item 10's evidence pointer.
- Commit: `docs(engineer): ADR-011 customer threat model and setup`.
