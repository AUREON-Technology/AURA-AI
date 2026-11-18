# Engineer agent: implementation plan (PLAN mode)

- **Status:** awaiting the owner's approval. Nothing here is implemented.
- **Base:** `main` at `0d6c53e`.
- **Decisions:** D-01…D-12 and T-1…T-8 (`02`).
- **Requirements:** `03`. **Gaps:** `04`.
- **File paths** below were located in the code during PLAN; line numbers move. "New" means the
  file does not exist yet.

## Progress (2026-11-17)

Built and tested locally on PR #251, which merged into `main` on 2026-11-18 (`d47e064`). Nothing
here ran in CI (no runners, CB-44), and nothing is proven live.

| WP | Status | Commits |
|---|---|---|
| WP0 | Done | `806534d` |
| WP6a | Done | `b5126b6` |
| WP3 | Done | `5cfb982`, merged with main's INT-002 in `d5c1791` |
| WP5 | Done | `2c3f9cb` (backend); the daemon half is in `5cfb982` |
| WP1 | Done; the notice text awaits the owner's review (O-03) | `bcdc378` (backend), `8ea3e45` + `8cc0e10` (client) |
| WP2 | Done | `bcdc378` (backend), client in `8cc0e10` |
| WP7 | Done in code; no real-machine Ubuntu check yet (that belongs to WP10a) | the client commit after `5c7022a` |
| WP8 | Done; ADR-011 amendment recorded | `b297dfb` (backend), `b3460c2` (daemon), client in the commit after `5c7022a` |
| WP4 | Done. D-13 (2026-11-18): a non-git folder offers "Initialize git here" instead of working unisolated | `f4426c0` (backend), `fe854c1` (daemon), client in the commit after `348f24c` |
| WP9 | Done, except G-13: running `desktop-package.yml` on a tag publishes a release and needs the owner's go-ahead. Already on main: G-18 (INT-003) and the `read_file` cap (INT-036) | `f4426c0` (1011 on server errors), `fe854c1` (approval queue), client in the commit after `348f24c` |
| Merge of main (Arabic plan) | Done; the consent migration was renumbered to `0023` after main's `0022` | `60cf1e9`, `3a9ef0c` |
| WP6 | Repo half done: the Railway runbook (`docs/deployment/control-plane.md`, "Claude for the Engineer"), the VPS example (`deploy/.env.example`, `hosted3`), and both compose files now forward `WORKLOADS` and `USER_TPD` (before, a VPS Claude slot booted unfenced and uncapped whatever `.env` said). **Owner half not done:** the Railway variables and the Anthropic key. Not accepted until the four live checks in that runbook pass | this branch, after `d47e064` |
| WP10a | Runbook written: `docs/guides/engineer-e2e-checklist.md` (10 sections, a report template). **Not run:** it needs WP6's Railway variables live (`claude1` absent from `/health/ready` on 2026-11-18), a real Windows 11 machine, a real Ubuntu 24.04 desktop and a phone, none of which this environment has. No report is committed until it is run | this branch |
| WP11, WP12, WP10b | Not started | — |

## 0. Ground rules for every work package

- **One work package (WP) = one PR**, drafted, with a test that fails before the fix. No test is
  skipped or weakened.
- **Local gates before every push**, in each touched tree. The results are quoted in the PR,
  which also says that CI did not run while CB-44 lasts.
  - **Backend:** `pytest` and `TESTING=True python -m unittest discover -p "test_*.py"`.
  - **Client:** `flutter analyze && flutter test`, with the CI-pinned SDK from CLAUDE.md.
  - **Daemon:** `dart analyze --fatal-infos && dart test`.
  - **Migrations:** `pytest tests/load -m load -k migration` against a throwaway Postgres 16.
  - **Redis-backed counters:** the matching `-m load` real-Redis test.
- **Every new route** gets `tests/security/test_route_auth.py` and `test_idor.py` entries, and
  `python scripts/api_reachability.py` is regenerated.
- **Railway auto-deploys every `main` push** and `main` is unprotected. A merge is a release, so
  anything behind a switch defaults to off.
- **Migrations:** numbered when written (T-8). #246 and #250 both take `0021` today.

## 1. Stages and dependencies

```
Stage 1 — Operators (Developer+), Windows + Linux, GitHub + desktop
  WP0 baseline ─────────┐
  WP1 consent ──────────┤
  WP2 per-user cap ─────┼──► WP6 Claude on (operators) ──► WP10a evidence (operators)
  WP6a workload fence ──┘          ▲
  WP3 daemon safety ──► WP4 isolation default
  WP5 GitHub branch guard
  WP7 Linux IDE
  WP8 remote control (needs WP3)
  WP9 robustness

Stage 2 — Paying customers (needs PR #246 merged, and every stage-1 WP3/WP8 item)
  WP11 plan-based access (T-6) ─┐
  WP12 desktop Firebase sign-in ┴──► WP10b evidence (customers) ──► customer launch
```

**Hard gates:**
1. WP6 turns Claude on only after WP1, WP2 and WP6a are **deployed**. Otherwise code goes to
   Anthropic without consent, without a cap, or for non-Engineer work.
2. No customer can pair a daemon before WP3, WP8 and WP9's G-18 part are deployed (SEC-12).
3. Stage 2 starts only after #246 has merged and its `PlanService` is live.

## 2. Stage 1 work packages

### WP0: Evidence baseline (G-20, part; NFR-03)
- **Do:**
  1. Regenerate `docs/guides/api-reachability.md` (`cd aura_backend && python
     scripts/api_reachability.py`), so `main`'s own suite is green again.
  2. In each later WP's PR, confirm Backend, Client and Host Daemon CI executed. Record their run
     ids in `docs/status/`.
  3. **Owner:** branch protection on `main` requiring those three checks.
- **Accept:** `tests/architecture/test_api_reachability.py` passes, and one green run of each CI
  is linked.

### WP1: Anthropic consent (G-03; SEC-02, DATA-01, UX-01, UX-02; T-2)
- **Backend:**
  - **Domain:** `EngineerConsent(user_id, notice_version, accepted_at, withdrawn_at)` in
    `aura_core/domain/engineering.py`.
  - **Error:** `ConsentRequiredError(AuraError)`, kind `consent_required`, HTTP 403, not
    retryable, in `aura_core/domain/errors.py`.
  - **Port:** `EngineerConsentStore` with `get`, `accept`, `withdraw` and `delete_for_owner`.
    - New in-memory adapter.
    - New Postgres adapter (`aura_core/adapters/postgres_engineer_consent.py`), following
      `PostgresPool._get_pool()`.
  - **Migration:** new `alembic/versions/00NN_engineer_consent.py`, table
    `aura_engineer_consents` (PK `user_id`), with a working `downgrade()`.
  - **Wiring:** `aura_core/container.py`, lazy, same as the other stores.
  - **Enforcement:** in `aura_core/kernel/kernel.py`, after `_resolve_agent`, when the workload
    is `ENGINEER`, raise if there is no current consent at `CURRENT_NOTICE_VERSION`. This covers
    stream, non-stream, `approval_ids` resume and delegation sub-turns (T-2).
  - **Routes** in `aura_core/api/v3.py`:
    - `GET /api/v3/engineering/consent` returns `{accepted, notice_version, current_version}`;
    - `POST` accepts with `{notice_version}`;
    - `DELETE` withdraws.
  - **`GET /api/v3/agents`** adds `consent_required: bool` to the `engineer` row.
  - **Erasure:** `api/admin_platform.py::_erase_core` deletes the consent row.
- **Client:**
  - New `lib/core/ai/engineer/engineer_consent_repository.dart`.
  - New `lib/features/engineering/presentation/engineer_consent_sheet.dart`. It shows the notice
    before the first Engineer send, from `desktop/agent/agent_panel.dart` and from the chat send
    path when the persona is `engineer`.
  - Settings → Engineer: show status, with a Withdraw button.
  - `chat_repository.dart` maps `consent_required` to the sheet, not to Retry.
  - Strings in `lib/l10n/app_en.arb`.
  - Correct the privacy copy changed in `32a6735` (onboarding page, "Where AURA runs").
- **Docs:**
  - ADR-033 amendment: Claude may hold `sensitive` for the Engineer workload only, behind
    consent.
  - ADR-030 §8.
  - `docs/deployment/control-plane.md` §3.
  - `docs/status/BACKEND_STATUS.md` row.
- **Tests:**
  - `tests/unit/test_engineer_consent.py`:
    - refused without consent, with no provider call;
    - accepted, then served;
    - withdrawn, then refused;
    - an old notice version is refused;
    - a delegated Engineer sub-turn is refused;
    - an approval resume is refused after withdrawal.
  - Store contract tests for both adapters.
  - Postgres round-trip under `tests/load`.
  - Route-auth and IDOR entries.
  - Client: `test/engineer_consent_sheet_test.dart`, plus the chat mapping test.
- **Prerequisite:** O-03, Anthropic's data terms for the notice text. The owner confirms the
  wording.

### WP2: Per-user daily token cap (G-08; NFR-02, FR-09; T-5)
- **Backend:**
  - **Setting:** `user_tpd` on the provider settings (`AURA_PROVIDER_<N>_USER_TPD`, 0 = off) in
    `aura_core/config/settings.py`.
  - **Counter:** in `aura_core/llm/quota.py`, a per-user counter keyed
    `(provider, user_key, UTC day)`.
    - Reserve the worst case (prompt estimate + `max_output_tokens`) and settle to the usage
      reported.
    - Persist through the cache port, like the daily provider counters.
  - **Router:** `aura_core/llm/router.py:547` passes `hint.user_key`. A candidate refused only by
    the user cap is excluded with reason `user_quota`. With no candidate left, raise
    `UserQuotaExhaustedError` (kind `user_quota_exhausted`, 429, `details.reset_at`). The refusal
    is not a circuit-breaker failure.
- **Client:** a distinct "Daily Engineer limit reached, resets at …" state in
  `chat_repository.dart`, with no Retry.
- **Tests:**
  - `tests/unit/test_user_quota.py`, with `FrozenClock`, covering:
    - reserve and settle;
    - refusal;
    - midnight reset;
    - two users isolated;
    - an unhinted call is never counted against a user.
  - `tests/load/test_user_quota_redis.py`: two processes share one counter.
- **Accept:** NFR-02's acceptance criteria.

### WP6a: Workload fence for the Claude slot (G-26; SEC-11; T-1)
- **Backend:**
  - `workloads: frozenset[str]` on the provider settings (`AURA_PROVIDER_<N>_WORKLOADS`, empty =
    all, as today).
  - The router's `_capability_filter` excludes the provider (`workload_not_allowed`) when the
    effective hint's workload is not in the set, **including an unhinted request**.
  - `_gate_claude` warns at boot when a Claude slot at `sensitive` has no `WORKLOADS`.
- **Tests:** `tests/unit/test_router_workload_fence.py`:
  - unhinted SENSITIVE → excluded;
  - INTERACTIVE → excluded;
  - ENGINEER → selected;
  - an empty set behaves as before.
  - Also add the missing pin: an Engineer turn is SENSITIVE from its start (`kernel.py:1208`).

### WP3: Daemon safety (G-04, G-05, G-06, G-16, G-17; SEC-03/04/05/08/09/10)
This is all `aura_hostd/` unless a path is given.

- **G-04 `task_key`:**
  - `lib/src/task_handler.dart::createTask` accepts only `^[A-Za-z0-9._-]{1,128}$`, not `.` or
    `..`.
  - `lib/src/isolated_task_registry.dart::resolve` re-checks containment of the resolved worktree
    in `parentRoot` and fails closed.
  - `createTask` errors instead of silently binding the live checkout when `resolve` returns
    `null`.
  - **Backend:** `ChatRequest.session_id` gets a pattern and a max length (`aura_core/api/v3.py`).
- **G-05 live folders:**
  - `bin/aura_hostd.dart` builds one `WorkspaceRoots` holder, shared by `ControlServer` and
    `TaskHandler`.
  - `lib/src/workspace_store.dart` exposes a reloadable view, and `control_server.dart`'s
    grant, revoke and trust update it in place.
  - Re-validate before every call (`task_handler.dart:457-466` becomes reachable).
- **G-06 trusted tier (D-06):**
  - New `lib/src/process_env.dart`: an allowlisted environment (PATH, HOME/USERPROFILE, LANG/LC_*,
    TEMP/TMP, SystemRoot/ComSpec on Windows, and nothing else). It is used by `run`, terminal
    sessions and diagnostics.
  - `lib/src/command_policy.dart`: in trusted folders, resolve every argument that looks like a
    path (any verb); outside the folder → `needsApproval`.
  - **Client:** honest label (UX-04) in
    `aura_client/lib/features/engineering/presentation/host_daemon_page.dart`.
- **G-16 process control:** tree kill on timeout and cancel in `task_handler.dart`, reusing
  `git_service.dart:591-602` (Windows) and process groups (`kill(-pid)`) on POSIX. Cancel kills a
  running command.
- **G-17 `.git` protection:**
  - Extend the denylist in `command_policy.dart:298-313` to `.git/modules/**/hooks/**`,
    `.git/modules/**/config`, `config.worktree`, and a worktree's `.git` gitdir file.
  - Allowlisted git runs with `-c core.fsmonitor= -c core.hooksPath=<empty dir> -c
    diff.external=`.
  - `lib/src/diagnostics_service.dart` runs only in trusted folders, with a timeout and the
    minimal environment.
- **Tests:** one per acceptance criterion in `03` (SEC-03…SEC-10), across `test/task_handler_test.dart`,
  `test/command_policy_test.dart`, `test/containment_test.dart` and the new
  `test/live_roots_test.dart`. Plus a backend `session_id` validation test.

### WP4: Isolated worktree by default (G-10; FR-08)
- **Client:**
  - `lib/core/ai/online_agents/sse_agent_client.dart::chatStreamBody` sends
    `desktop_task_isolation: true` for Engineer turns from the IDE panel and from remote turns.
  - Fix the `desktop/control_center/control_center_panel.dart` empty state.
- **Daemon:** a worktree reuse test across turns of one `session_id`.
- **Accept:** a client request-body test, and the end-to-end item in WP10.

### WP5: Default-branch guard (G-07; SEC-06, FR-07)
- **Backend:**
  - `aura_core/tools/coding.py::github_commit_files` refuses the repository's default branch,
    with the same message as `git_push`.
  - `aura_core/adapters/github_client.py` (`:168-200`) builds one commit through the Git Data
    API: blobs → tree → commit → update ref, without force. The run record gets that SHA (G-23).
- **Daemon:** `command_policy.dart` classifies any agent `git push`, including `git -C … push`
  and pushes inside `&&`/`;` chains, as `needsApproval` in every tier.
- **Tests:**
  - `tests/unit/test_engineer_tools.py`: default branch refused; one commit for N files.
  - `tests/security/test_github_boundary.py`.
  - Daemon `command_policy_test`.

### WP6: Turn Claude on for operators (G-01, G-02; NFR-01, FR-01, NFR-06)
- **Order:** only after WP1, WP2 and WP6a are deployed.
- **Owner (Railway dashboard):**
  - `AURA_ALLOW_CLAUDE_INFERENCE=true`.
  - A new slot `AURA_PROVIDER_CLAUDE1_*` with:
    - `KIND=claude`, a Claude model ID;
    - `API_KEY_ENV`;
    - `MAX_DATA_CLASSIFICATION=sensitive`;
    - `POOLS=coder`;
    - `WORKLOADS=engineer`;
    - `RPM`, `TPD`, `USER_TPD`.
  - Add `claude1` to `AURA_LLM_PROVIDERS`.
  - `AURA_ENGINEER_AGENT_ROLES=Developer,Admin,SuperAdmin`.
  - Confirm `AURA_REDIS_URL` is set and that boot logs no "host-agent relay off" warning.
- **Repo:**
  - `docs/deployment/control-plane.md` documents every one of these settings.
  - `deploy/.env.example` and both compose files get the slot as commented examples. Keep the
    prod and dev compose files at parity (CLAUDE.md).
- **Accept:**
  - `/health/ready` lists `claude1` as healthy.
  - A live operator Engineer turn completes on `claude1`.
  - `/api/v3/agents` shows `usable:true` for a Developer.
- **Rollback:** remove `claude1` from `AURA_LLM_PROVIDERS`. Engineer turns go back to
  `capability_unavailable`, and nothing else changes.

### WP7: Desktop IDE on Linux (G-09; FR-10)
- **Client:**
  - `lib/core/platform/aura_platform.dart`: `isDesktopIde` covers Linux.
  - `core/routing/router.dart:612-626`: macOS and web show a "not supported yet" page instead of
    blank.
  - `test/desktop/desktop_shell_smoke_test.dart` adds the Linux platform.
- **Packaging:** `.github/workflows/desktop-package.yml` Linux tar.gz, run on a `desktop-v*` tag.
- **Real machine (Ubuntu 24.04):** IDE, PTY terminal, file dialog, supervisor restart, installer
  tar.gz. The results go into the WP10 report.

### WP8: Remote desktop control (G-11; FR-11, SEC-07, UX-06; T-3, T-4)
- **ADR-011 amendment first**, recording T-3 and T-4 and the accepted residual risk (D-10c).
- **Backend:**
  - The v3 `Principal` (`aura_core/api/v3.py:130`) gains `platform` and `device_id`, from claims
    set only by `services/jwt_auth.py`. The authenticator in `aura_service.py` copies them.
  - The turn origin is `desktop` iff `platform == "desktop"`, else `remote`.
  - `adapters/host_agent.py::create_task` sends `origin`.
  - `tools/policy.py`: `host_routed` with `origin == remote` holds **every** workspace-local side
    effect for confirmation, tainted or not.
  - New `GET /api/v3/host/folders` relays the daemon's granted folders and `remote_control`
    flag through the session/relay.
- **Daemon:**
  - A `remote_control` flag in `config.json`, settable only via the local control server.
  - `TaskHandler` refuses an `origin=remote` task when the flag is off, and never applies the
    trusted tier to one.
- **Client:**
  - A desktop toggle, "Allow control from my phone and the web", on the daemon page and in the
    Host panel.
  - A mobile/web folder picker in Engineer chat, which sends `workspace_root`.
  - Daemon status, connected and remote on/off (UX-06).
  - Approvals use the existing `EngineerConfirmationCard`.
- **Tests:**
  - A forged `platform` body field is ignored.
  - Remote with the flag off → refused.
  - Remote with the flag on → every write and command is pending.
  - Toggling off while connected → the next call is refused.
  - A desktop-origin turn behaves as before.

### WP9: Robustness (G-12, G-14, G-15, G-18, G-21, G-13)
- **Client:**
  - Rehydrate `pendingConfirmationProvider` from persisted `turnMetaJson.pending_tool_calls`
    until the server TTL.
  - Honor `retryable:false` (`confirmation_invalid` → "approval expired").
  - Keep the held turn's persona label on resume.
  - De-duplicate steps by `step_id` (`chat_repository.dart:463`, `step_log_panel.dart`).
  - Make the status-bar daemon item tappable, opening Re-pair.
  - Pop the pairing dialog on error (`host_panel.dart`, `host_daemon_page.dart`).
  - Invalidate `agentsProvider` on resume and pull-to-refresh.
- **Daemon:**
  - `/status` reports `refused` after 4401.
  - Prune `ApprovalQueue` on decision.
  - Cap `read_file` at 2 MB.
- **Backend:**
  - `api/host_agent.py:82-85` closes with 1011 on a server error and keeps 4401 for auth.
  - `api/v2/auth.py::approve_desktop_pairing` requires `engineer_agent` (or T-6's entitlement in
    stage 2).
- **Packaging:** run `desktop-package.yml` on a tag (G-13). O-01 decides signing.

### WP10a: Operator end-to-end evidence (NFR-04)
- **Write:** a runbook, `docs/guides/engineer-e2e-checklist.md`.
- **Run it** on a real Windows 11 machine and a real Ubuntu desktop against
  `api.aureon-ai.tech`, as a Developer, covering:
  - pair, consent, isolated write/command/merge;
  - approval round-trip;
  - GitHub branch → one commit → draft PR;
  - default-branch refusal;
  - folder revoke while connected;
  - remote on/off from a phone;
  - the cap message;
  - Anthropic down (remove the slot) → clear state.
- **Commit:** `docs/reports/desktop-engineer-e2e-operators-<date>.md`. Then update GAP-026,
  `docs/status/DESKTOP_APP_STATUS.md` and ADR-011's status line.

## 3. Stage 2 work packages (customers; needs #246 merged)

### WP11: Plan-based Engineer access (G-27; FR-01 customers, FR-18, FR-19, SEC-12; T-6)
- **Backend:**
  - **Entitlement checks** in `aura_core/plans/service.py` (#246):
    - `engineer_entitled(principal)`: the role is in `engineer_agent_roles`, or the active
      workspace's plan has `engineer`;
    - `engineer_entitled_anywhere(user_id)`: any membership's plan has `engineer`.
  - **Authenticator** (`aura_service.py`): after workspace resolution, add `engineer_agent` when
    `engineer_entitled`. #246's `admit_turn` removal stays as defence in depth.
  - **Desktop sign-in and refresh, pairing approval** (`api/v2/auth.py:207-251, 274-285`): admit
    an operator, or anyone `engineer_entitled_anywhere`. A downgraded customer is refused at the
    next refresh, with a plain reason.
  - **Host socket** (`api/host_agent.py`): the 60 s re-check uses `engineer_entitled_anywhere`.
  - Cache entitlements briefly (≤ 60 s) so the socket re-check is not a DB call per frame.
- **Docs:** ADR-011 amendment (F-09 narrowed for plan-entitled customers); #246's
  `docs/deployment/plans-and-limits.md` says Engineer is real for Pro/Team.
- **Tests:**
  - A Pro customer: usable, signs in on desktop, pairs, socket stays open.
  - A Free customer: refused everywhere.
  - Downgrade mid-session: refresh refused, socket 4401 within one re-check.
  - A Team member with a Free personal workspace can pair.
  - Customer Engineer turns are metered; operators are not.

### WP12: Desktop sign-in through Firebase (G-28; FR-20; T-7)
- **First, verify** the `firebase_auth` plugin's Windows and Linux support. If native provider
  sign-in works on both, use it. Otherwise do the following browser hand-off.
- **Web:** a hosted sign-in page (the web build's Firebase sign-in, from #246's
  `identity_service.dart`) that, on `?desktop_redirect=http://127.0.0.1:<port>/…&state=…`, posts
  a one-time code to the loopback.
- **Backend:** `POST /api/v3/auth/desktop/firebase-exchange` takes the one-time code → verifies
  the Firebase ID token server-side (#246's verifier) → applies the T-6 entitlement check →
  `issue_desktop_session`. The code is single-use, expires in 60 s, and is bound to `state` and
  PKCE.
- **Client:** `lib/core/auth/desktop_oauth_client_io.dart` offers Google, Apple and email via the
  hand-off. The existing Google path keeps working until it is retired.
- **Tests:**
  - Exchange: single use, expiry, state mismatch, Free-plan refusal.
  - The client loopback handler.
  - Real machine: all three providers on Windows and on Linux.

### WP10b: Customer end-to-end evidence
Repeat WP10a as a Pro customer and as a Free customer (expect refusal), plus a downgrade
mid-session. Commit `docs/reports/desktop-engineer-e2e-customers-<date>.md`. Only then open the
customer launch.

## 4. After launch (P3)

G-22 (docs: ADR-011, ADR-014, hostd README, `docs/status/` apex domain), G-23 (run records),
G-24 (minor code defects), G-25 / O-02 (Arabic; #250 already plans `lib/desktop` and the daemon
in its stages 2–3), DEF-01 / G-19 (server workspaces, including host-git hooks).

## 5. Rollout and rollback

| Step | Switch | Rollback |
|---|---|---|
| WP1, WP2, WP6a merged | Inert until a Claude slot exists. Consent is checked but no Engineer turn can run yet. | Revert the PR |
| WP3, WP5, WP9 daemon changes | Ship in a new desktop build. Old daemons keep working; the backend still accepts them. | Previous desktop release via `desktop-update.json` |
| WP6 | `claude1` in `AURA_LLM_PROVIDERS` | Remove it |
| WP8 | Daemon flag defaults to off, so nothing changes until a user opts in | Users toggle off; a backend revert re-holds nothing |
| WP11 | Depends on #246's plan catalogue: Free has no Engineer | Set `AURA_PLAN_CATALOGUE` with `engineer:false` for Pro/Team |
| WP12 | A new sign-in option alongside Google | Hide the option in a client release |

## 6. Risks to watch

- **Anthropic is the only `sensitive` Engineer provider.** An outage or TPD exhaustion means no
  Engineer. FR-09 must make that state clear.
- **#246 and #250 collide on migration `0021`.** Re-check the head before writing WP1's
  migration.
- **WP12 depends on the plugin check in T-7.** If the browser hand-off is needed, it is the
  largest single package. Customers are the only users blocked by it; operators can keep Google.
- **CI may still not get runners (CB-44).** Every WP then carries local evidence only. Treat
  NFR-03 as unmet until it does.

## 7. Approval needed

Approve this plan (or name the WPs to start) to enter IMPLEMENT. Suggested first batch, which is
independent and safe to merge while inert: **WP0, WP6a, WP3, WP5**. Then WP1 and WP2, which need
O-03 for the notice text.
