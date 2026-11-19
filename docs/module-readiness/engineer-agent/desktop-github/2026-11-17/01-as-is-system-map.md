# Engineer agent: as-is system map

`main` at `0d6c53e`, 2026-11-17. Code read, plus the live checks named below. Paths are
repo-relative.

## Layers and commands

| Layer | Path | Stack | Commands used here |
|---|---|---|---|
| Backend gateway | `aura_backend/` | Python 3.11, FastAPI, hexagonal | `pip install --require-hashes` (both lockfiles); `pytest` on Engineer suites |
| Desktop / mobile / web client | `aura_client/` | Flutter 3.44.6, Riverpod, Drift | Code read only (no Flutter SDK run this session) |
| Host daemon | `aura_hostd/` | Pure Dart 3.12.2 | Code read only (no Dart SDK run this session) |
| Production | Railway, `https://api.aureon-ai.tech` | | `curl /health/live`, `/health/ready`, `/api/v3/agents` |

## Live state (checked 2026-11-17)

- **`api.aureon-ai.tech/health/ready`** returns 200 with:
  - `healthy: true`;
  - schema `0020`;
  - git sha `0d6c53e` (current `main`);
  - providers `hosted1` (healthy, `is_local: false`) and `hfspace` (`HTTP 503`);
  - `local_healthy: false`;
  - 37 tools and 8 agents.
- **`aureon-ai.tech`** (the apex) returns Railway's `404 Application not found`. The backend moved
  to `api.` on 2026-11-14 (`aura_client/lib/core/config/backend_url_settings.dart:20`).
  `docs/status/` still names the apex.
- **`/api/v3/agents`** returns 401 without a token, so whether any user holds `engineer_agent` was
  not observable.

## Flow 1: Engineer chat turn (desktop or GitHub)

```
Client  POST /api/v3/chat/stream {agent:"engineer", session_id, workspace_root?, approval_ids?}
  ↓     (aura_client/lib/core/ai/online_agents/sse_agent_client.dart:134-183)
Auth    Principal capabilities: engineer_agent iff role ∈ AURA_ENGINEER_AGENT_ROLES
        (aura_service.py:128-129; settings.py:1214, default empty)
  ↓     no capability → AgentRegistry.resolve silently falls back to orchestrator (registry.py:118-130)
Kernel  baseline classification SENSITIVE for the ENGINEER workload (kernel.py:1208-1209)
  ↓
Router  drops every provider whose ceiling < SENSITIVE (router.py:359-361);
        none left → CapabilityUnavailableError → 503 capability_unavailable (router.py:708-713)
  ↓     ← production stops here today: hosted1 ceiling = internal, hfspace 503
Plan → execute tools (≤ 8 replans, tool budget 216, loop deadline 960 s; registry.py:230-280)
  ↓
Tools   tools/coding.py — write/edit/run/git_* route to the sandbox runner;
        github_* call the GitHub API with the user's OAuth identity
  ↓
Runner  CompositeSandboxRunner: paired desktop session first, else Docker (off on Railway),
        else error "no desktop app is connected…" (adapters/host_agent.py:461-487)
  ↓
Daemon  /api/v3/host/agent WebSocket → create_task / run / write_file(s) / read_file
        (aura_hostd/lib/src/rpc_client.dart:176-200)
  ↓
SSE     token, tool_calls, step, actions, citations, revision, error, done{pending_tool_calls}
```

## Flow 2: confirmation

1. A side-effecting call that needs confirmation returns `needs_confirmation`. The kernel stores
   the plan (Redis cache port, 1 h TTL, single-use, digest-bound;
   `execution/confirmations.py:70,101-129`) and returns `pending_tool_calls[].approval_id`.
2. The client renders `EngineerConfirmationCard` from an **in-memory** provider
   (`chat_repository.dart:543-555`).
3. **Approve** re-POSTs `/chat/stream` with `approval_ids`. The kernel runs the stored plan
   without re-planning, keeping the stored persona and root (`kernel.py:1773-1804`).
4. **Deny** is local only; the held turn expires on the server.

**What forces confirmation:**
- `destroy_workspace`, `git_push`, `clone_repository`, and the `github_*` writes;
- MCP tools not listed as safe;
- on the desktop, any workspace-local write or command in a tainted turn
  (`tools/policy.py:131-136`). The first `run_command` taints the turn.

**What does not force confirmation:** in a desktop folder marked trusted, writes and commands
pass straight through; only the daemon's own command tiers apply.

## Flow 3: desktop daemon

- **Pairing:** device grant. `pair/start` and `pair/poll` are unauthenticated; `pair/approve`
  needs a JWT and does not check `engineer_agent` (`api/v2/auth.py:274-285`). The token is
  30 days and device-scoped.
- **Connection:** the WebSocket closes with 4401 on a missing capability, an expired or revoked
  token, or **any** auth exception (`api/host_agent.py:82-92`). The daemon stops for good on 4401
  (`rpc_client.dart:89-92`).
- **Cross-worker relay:** Redis relay when `AURA_REDIS_URL` and a JWT secret are set
  (`container.py:1419-1447`); per-process otherwise.
- **Folders:** granted folders (canonicalised, filesystem roots denied) with trusted and
  untrusted tiers (`command_policy.dart:215-288`).
  - The agent's handler is given a **snapshot** of the folders at connect time
    (`bin/aura_hostd.dart:177`). The control server writes changes to disk
    (`control_server.dart:256-277`), and the handler never sees them.
- **Local approval queue:** 5 min timeout. The only UI for it is the Windows IDE status bar
  (`desktop/shell/status_bar.dart:43,243`).
- **Isolated worktrees:** `<root>/.aura-workspaces/<task_key>`, with `task_key = session_id`
  (`adapters/host_agent.py:299-301`). The client never asks for them (0 hits for
  `desktop_task_isolation`).

## Flow 4: GitHub

- **Connection:** per-user OAuth (`github_identities`, alembic `0005`). The client connects,
  checks status and disconnects (`github_connection_page.dart`).
- **Read tools:** list files, get file, get issue, get PR. These taint the turn.
- **Write tools:** `github_create_branch`, `github_commit_files` (one commit per file, no
  default-branch refusal; `github_client.py:168-200`) and
  `github_create_draft_pull_request`. All of them always need confirmation.

## Client surfaces

| Surface | Platform | State |
|---|---|---|
| Desktop IDE: Engineer panel, folder binding, terminal, approvals dialog | Windows only (`core/platform/aura_platform.dart:14`) | Built. `/ide` renders blank elsewhere (`router.dart:612-626`). |
| Chat persona picker | All | Built. Locks `usable:false`. Sends no `workspace_root`, so the backend uses the daemon's first root. |
| Settings → Engineer: history, GitHub, daemon page | All (daemon page breaks on mobile: Start spawns a process) | Built |
| Agent Review / Control Center (worktrees) | Windows IDE | Built, never fed (isolation off) |

## Tests (local run, this session)

- **Backend, 22 Engineer-related files plus `tests/architecture`, locked env:** **796 passed,
  1 failed.** The failure is `test_api_reachability::test_the_committed_matrix_is_current`:
  `docs/guides/api-reachability.md` on `main` is stale after #243 added
  `/api/v3/auth/desktop/exchange`. The diff is kept in this session's scratchpad. The doc was
  restored, so this report changes no tracked doc.
- **hostd (from the 2026-11-16 agents audit, not re-run):** 274 passed.
- **Client (from #244's commit, not re-run):** 700 passed, 4 skipped.
- **CI:** Backend CI and Host Daemon CI have not executed since `dc87beb`. Client CI was green
  on #213–#215 (2026-11-08).
- **No test covers:** the desktop `host_panel`, `host_daemon_page`, `github_connection_page`,
  `engineer_history_page` and `control_center`; daemon `bin/aura_hostd.dart` (including
  `restart()`); `task_key` validation; root revocation reaching a live agent; the Engineer →
  SENSITIVE baseline (`kernel.py:1208`).

## Evidence map

| Area | Evidence | Confidence |
|---|---|---|
| Client | Subagent code map; three claims re-checked by hand (isolation flag, step duplication, confirmation rehydration) | High for re-checked, Medium for the rest |
| Backend | Subagent map; SENSITIVE baseline, `task_key` passthrough and host-git env re-checked by hand; 796 local tests | High |
| Daemon | Subagent map; `task_key` join and root snapshot re-checked by hand | High for re-checked, Medium for the rest |
| Data | `aura_engineer_runs` (0016), `aura_agent_runs` (0008), confirmations in Redis | Medium |
| Authorization | Capability gating read; `pair/approve` ungated | High |
| Operations | Live `/health/ready`; Railway env not visible | Medium |
