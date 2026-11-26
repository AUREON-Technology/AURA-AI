# Tool engine audit — 2026-11-16

**Baseline:** `main` @ `ea04ab9` · **Scope:** every tool the backend registers, how each is called (kernel, planner/executor, agentic loop, delegation, confirmations, desktop daemon, MCP, blueprints, clients), and what is broken or missing.
**Builds on:** pass C of the full-system audit (`../full-system-audit/tool_inventory.md`, baseline `ffbbb9f`, 2026-10-31). Pass C read the code but ran nothing. This audit executes the real registry.

## How this was checked

| Evidence tag | Meaning |
|---|---|
| **RUN** | Executed against the real `Container` / `ToolRegistry` on the locked Python 3.11 environment (`probe_tools.py` in this folder, plus the scaled timing reproductions below) |
| **CODE** | Read and confirmed in source at `ea04ab9` |
| **TEST** | Existing suite: the 32 test files that touch the tool engine plus `tests/architecture`, run locally. Exit 0 (all passed). **Not a CI run**: Backend CI has not executed since `dc87beb` (CB-44). |

Nothing here was run against a live deployment. Statements about Railway are inferred from `docs/status/` (no Docker, no Ollama) plus the container's wiring.

## 1. What is registered

The default container (in-memory, no opt-ins) registers **37 tools** (RUN). With `AURA_GITHUB_ENABLED` + a token, **45** (RUN, registration only). The workspace tools (`git_push`, `open_workspace`, `list_workspaces`, `destroy_workspace`) need `AURA_SANDBOX_ENABLED` **and** a reachable Docker daemon. MCP tools need `AURA_MCP_ENABLED` and server config. Blueprint tools (`bp<ver>_*`) appear on deploy.

### 1a. Live status of each tool (RUN, default container, engineer capability)

| Tool(s) | S | SE | Result when called |
|---|---|---|---|
| `calculator`, `current_datetime` | PUBLIC | – | ✅ correct output |
| `prioritize_tasks`, `find_calendar_slots` | PERSONAL | – | ✅ correct output (`prioritize_tasks` requires `id` per task, as documented) |
| `propose_task/event/expense/income/health_log` | PERSONAL | ✓ | ✅ emits `{type, params}` action. ⚠ accepts invalid dates and empty health logs (T-15) |
| `create/list/update/delete` × task, calendar_event, finance_record, health_record (16) | PERSONAL | ✓ (not list) | ✅ full create→list→update→delete→list chain passed in all four domains. ⚠ in-memory store accepts what Postgres rejects (T-15) |
| `search_knowledge`, `recall_memory`, `remember_fact` | PERSONAL | `remember_fact` | ✅ |
| `delegate_to_agent` | SYSTEM | – | ✅ runs a sub-turn. Granted to no persona explicitly; reachable via orchestrator's open grant |
| `write_file`, `write_files`, `edit_file`, `run_command`, `git_diff`, `git_status`, `git_commit`, `git_checkout_branch` | PERSONAL/SYSTEM | mostly ✓ | ❌ with no paired desktop and no Docker sandbox (= Railway today), every call fails: *"no desktop app is connected and no server-side sandbox is configured"* (T-13) |
| `clone_repository`, `github_*` (7) | CONFIDENTIAL | mixed | Registered only with GitHub configured. Not executed (would call GitHub) |
| `git_push`, `open/list/destroy_workspace` | – | – | **Never registered** without Docker. Granted to `engineer` anyway (T-14) |
| `fetch_url`, `get_weather`, `list_files`, `read_file` | – | – | **Never registered in any configuration** (T-06) |

### 1b. Persona grants vs registry (RUN)

| Persona | Granted but not registered (default container) |
|---|---|
| `research_agent` | `fetch_url` — its **only** network tool, and it cannot be enabled (T-06) |
| `engineer` | 12 names: the GitHub, clone, push and workspace tools. With GitHub configured, 4 remain (`git_push` + workspace trio) unless Docker exists |
| everyone else | none |

## 2. How tools are called

1. **Chat turn → planner → `ExecutionEngine` → `ToolRegistry.execute`.** This is the only production execution site (`execution/engine.py:510`). Model-emitted `tool_calls` are never executed: generation sends `tools=()` (`kernel.py:873`), and streamed `tool_calls` are only reported (ADR-006, OK).
2. **Agentic loop** (`engineer`, `max_plan_iterations > 1`): plan → act → observe → replan, bounded by `RunBudgetGate`.
3. **Delegation**: `delegate_to_agent` recurses into `kernel.run()` with depth +1. Taint is now inherited (`delegation.py:144-147`).
4. **Confirmations**: a held call returns `needs_confirmation` + `pending_tool_calls` in the stream's `done` frame. The client resends the message with `approval_ids` (`sse_agent_client.dart:154` ↔ `ChatRequest.approval_ids`). Voice uses a `confirm` frame. Both match field for field.
5. **Desktop daemon**: host-routed tools map onto five RPCs (`create_task`, `run`, `write_file`, `write_files`, `read_file`). Server and daemon agree in both directions.
6. **Client-applied actions**: `propose_*` results become an `actions` SSE frame that `pending_actions_executor.dart` applies. All 5 type strings and every param key match.
7. **MCP**: tools are discovered once at `Container.startup()` as `mcp_{server}_{tool}`.
8. **Blueprints**: `bp<ver>_*` tools are registered on deploy and retired after `ADAPTATION_TOOL_GRACE_S`.
9. **HTTP**: `GET /api/v3/tools` lists tools. No route executes a tool directly.

`execute_many` has **no production caller**: the executor runs its own `asyncio.gather`. Its taint-deferral logic is exercised only by `tests/security/test_concurrent_taint.py`.

## 3. Findings

Severity follows the repo's P1–P4 scale. Each finding is marked **BROKEN** (does the wrong thing) or **GAP** (missing or unreachable).

### P2

**T-01 · FIXED in this PR · Engineer tools are cut off far below their declared budget.** (RUN + CODE)

> **Fix:** `AIKernel._tool_context` now gives an agentic persona (`max_plan_iterations > 1`, today `engineer`) `engineer_loop_deadline_s` (960 s) as its per-call ceiling. The new `ToolRegistry.call_timeout` adds the approval window *on top of* a host-routed call's budget instead of taking the `max`. Result: `run_command` gets 605 s in the sandbox and 605 + 330 = 935 s on a desktop, which is still within the 960 s loop deadline. Conversational personas keep 60 s.
>
> **Tests:** `tests/unit/test_engineer_tool_deadline.py` drives the kernel's real context with the registered `run_command` spec, plus scaled real-sleep registry cases. The registry test failed before the change.
>
> **Follow-up (2026-11-26):** `AIKernel._tool_context` now also gives an unrestricted turn (`orchestrator`) whose caller holds `engineer_agent` the `engineer_loop_deadline_s` ceiling, so direct Engineer tool calls from the orchestrator are not truncated at `delegation_deadline_s` (180 s) either.

Original finding:
- `registry.py:369` computes `timeout = min(spec.timeout_s, context.deadline_s)`, and `kernel.py:1544` gives every non-delegating persona `deadline_s = 60`.
- **Server-sandbox path:** `run_command` declares 605 s and tells the model it may run up to 600 s (`coding.py:585-591`), but it is killed at **60 s**.
- **Desktop path:** the call gets `max(60, 330)` = **330 s in total**, approval wait included. `settings.py:1081-1085` and `test_host_tool_cancellation.py:129` both say the requirement is 605 s run + 330 s approval.
- **Reproduction** (timings scaled ×0.01, real `ToolRegistry`):
  - a 100 s command in the sandbox times out at 60 s;
  - 1 min approval + 5 min run on the desktop times out at 330 s.
- **Why the tests pass:** they always build `ToolContext(deadline_s=5.0)`. They never use the kernel's real context, and they only pin the *outer* loop deadline.
- **Fix direction:** derive the per-call deadline from the persona / loop budget (e.g. `engineer_loop_deadline_s`) rather than a flat 60. Add a test that goes through `AIKernel._tool_context`.

**T-02 · FIXED in this PR · A server-confirmed call routed to the desktop loses the approval window.** (RUN + CODE)

> **Fix:** `ToolRegistry.execute` now computes `on_desktop` (routing) separately from the policy input `host_routed = on_desktop and not confirmed`. The timeout and the semaphore bypass follow routing, so a confirmed desktop call gets the approval window too. Policy decisions and audit records are unchanged.
>
> **Not changed, deliberately:** no "pre-approved" signal goes to the daemon. Its local approval stays authoritative.
>
> **Tests:** `test_engineer_tool_deadline.py` has 3 new cases. The confirmed desktop case failed before the fix.

Original finding:
- `host_routed` includes `and not confirmed` (`registry.py:322-328`).
- That is right for *policy*: no second server-side prompt. But the same flag also drives the *timeout*, so the call falls back to 60 s.
- **When it happens:** a tainted context needs confirmation for desktop writes (`policy.py`). After the user approves in the app, the resumed call runs with `confirmed=True`.
- **What then goes wrong:** the server gives up at 60 s. The daemon has no "already confirmed" signal (no such field in `adapters/host_agent.py` or `aura_hostd/lib`), so when its own `command_policy` says `needsApproval` it still asks the human, for up to 5 min. A late Approve then runs with nothing upstream waiting: the exact failure FSA-041 fixed, reopened for this path.
- **Reproduction:** confirmed + host-routed, 200 s of work → timeout at 60 s.
- **Fix direction:** compute the routing used for the timeout without `not confirmed`. Separately, consider passing a pre-approved flag to the daemon.

**T-03 · FIXED in this PR · The `engineer` persona cannot use any MCP tool.** (CODE)

> **Fix:** `AgentSpec.allowed_tool_prefixes`; only `engineer` gets `{"mcp_"}`, and an empty prefix is rejected. Every checkpoint asks one helper, `domain/tools.py::tool_grant_permits`: the kernel's declarations, `ToolContext.permits_tool` → `ToolPolicy.evaluate`, `AgentSpec.permits_tool`, and the blueprint gate.
>
> **Delegation:** the kernel's delegated grant and `delegate_to_agent` both use `intersect_tool_grants`. A prefix survives only when both sides hold it, so delegating to the engineer cannot hand MCP tools to a caller without them.
>
> **Unchanged:** MCP sensitivity and confirmation, and generated blueprint agents may not hold prefixes.
>
> **Tests:** `test_agents.py` (the grant pin now covers MCP builders) and `test_delegation.py`. The pin failed before the fix.

Original finding:
- Its grant (`agents/registry.py:220-235`) is an exact-name list with no `mcp_*` entries.
- The comment above it claims it covers "any MCP-bridged tool", but grants are exact-name matches (`kernel.py:1518`).
- **Effect:** only `orchestrator` (open grant) can reach MCP tools, and only for an `engineer_agent` caller, without the engineer loop or prompt.
- `tests/unit/test_agents.py:307` pins the grant against the sandbox and GitHub builders only, so it cannot catch this.

**T-04 · FIXED in this PR · An MCP server that dies after startup hangs every call to it.** (CODE)

> **Fix:** when `_ServerSession._run` exits, every queued and in-flight request fails with `McpConnectionError`, which carries the cause, and later calls raise at once. `McpClient` evicts a dead session and reconnects lazily on the next call: one attempt per call, no background loop. `isError=True` now raises `McpToolError`, so the registry reports `ok=False` with the server's text.
>
> **Tests:** `test_mcp_client_failure.py` (4) runs against an in-process fake server through a new injectable `session_factory`; the default factory is the unchanged SDK path. All 4 failed before the fix, the hangs showing as 1 s `wait_for` timeouts.
>
> **Not tested:** against a real MCP server.

Original finding:
- When `_ServerSession._run` exits it sets `_failed`, but `call_tool` (`mcp_client.py:~110`) never checks `_failed`. It just queues a request nobody reads, so every call waits until the registry timeout.
- There is no reconnect, no health check and no unregistration, so dead tools stay advertised.
- Separately, an MCP result with `isError=True` and text content is returned as an ordinary string (`mcp_client.py:185-191`), so the registry reports `ok=True`.
- None of this is tested in the default suite.

**T-05 · FIXED in this PR · A blueprint rollback inside the grace window fails.** (CODE)

> **Fix:** `BlueprintToolMaterializer` tracks pending retirements per version and the live version. `activate()` cancels a version's pending retirement, reuses a complete registered set, and re-registers a partial one cleanly. `retire()` never removes the live version's tools, and re-checks that when the timer fires. A failed go-live that reused tools reschedules their retirement. Deploy, rollback and `_load`/`sync` all go through `activate()`.
>
> **Tests:** `TestGracePeriod` (6, `grace_s=0.2`, real waits). 4 failed before the fix with the duplicate-registration `ValidationError`.
>
> **Scope:** coordination is per process. Each worker has its own registry and shares the store, which matches the existing model.

Original finding:
- `_go_live_locked` calls `materializer.register()` without checking `names_of` (`adaptation/deployment.py:254`).
- If the target version's tools are still registered, because its retirement is pending (default 300 s), `ToolRegistry.register` raises on the duplicate and the rollback errors out.
- **Related (reported by the sub-investigation, not independently confirmed):** a `sync()` on another worker skips registration because the names exist (`deployment.py:221`), but never cancels the pending retire task (`runtime.py:423-428`). That task later unregisters tools the live agents depend on.
- Every adaptation test uses `grace_s=0`, so neither case is exercised.

### P3

**T-06 · FIXED · `fetch_url`, `get_weather`, `list_files` and `read_file` can never be enabled.** (RUN + CODE; pass-C C-08)

> **Fix:** `ToolSettings.files_root` (`AURA_TOOLS_FILES_ROOT`), `ToolSettings.http_allowed_hosts` (`AURA_TOOLS_HTTP_ALLOWED_HOSTS`), and `ToolSettings.weather_enabled` (`AURA_TOOLS_WEATHER_ENABLED`) are now wired through `Container._build_tools` (`container.py`) into `default_tools`. `list_files` and `read_file` are granted to `engineer` and `research_agent` (`agents/registry.py::_DOCUMENT_READING_TOOLS`), and every registered tool is pinned against persona grants or `_ORCHESTRATOR_ONLY_REGISTERED_TOOLS` in `tests/unit/test_tools_review_engine.py` and `tests/unit/test_builtin_tools_registration.py`.

Original finding:
- `Container._build_tools` (`container.py:1453-1466`) never passes `workspace`, `allowed_http_hosts` or `enable_weather` to `default_tools`.
- `ToolSettings` has no field or env var for any of them.
- `research_agent`'s only network tool is therefore dead, and `echo.py:15`'s scripted `get_weather` call refers to a tool that never exists.

**T-07 · MITIGATED · Engineer tools are reachable under `orchestrator` without the engineer loop.** (CODE; C-03)

> **Fix:** `AIKernel._tool_context` now grants `engineer_loop_deadline_s` (960 s) whenever the caller holds `engineer_agent` and the resolved persona permits `run_command` (which covers both `engineer` and `orchestrator`), so direct Engineer tool calls from an orchestrator turn get the full Engineer per-call timeout (`tests/unit/test_engineer_tool_deadline.py`).

Original finding:
`orchestrator` has `allowed_tools=None` and a SYSTEM ceiling, so an `engineer_agent` caller gets all sandbox, GitHub and MCP tools there with a single-pass plan and the generic prompt.

**T-08 · FIXED · `TOOLS_MAX_CALLS_PER_TURN` is only partly enforced.** (CODE; C-05)

> **Fix:** `AIKernel._turn_budget_and_gate` (`kernel.py`) builds the effective `AgentBudget` and a `RunBudgetGate` for every execution path -- both the standard `agentic_executor` loop and the direct `executor.execute` / `executor.stream` fallback -- and chains a delegated sub-turn's `RunBudgetGate` to its parent turn's gate via `TurnLookups` (`kernel/turn_scope.py`), so delegation cannot reset the root turn's `TOOLS_MAX_CALLS_PER_TURN` cap. `tests/unit/test_kernel_tool_budget.py` now pins real tool-dispatch refusals on the loop, on the direct executor fallback, and across delegation.

Original finding:
- It now becomes the default `tool_budget` in `_loop_arguments` (`kernel.py:2641-2643`), enforced by `RunBudgetGate` across every persona on the standard agent loop.
- **Not applied** on the direct `executor.execute` fallback (used only when no `AgentRegistry` or no `agentic_executor` is wired).
- **Not cumulative across delegation:** each sub-turn gets a fresh cap.
- `test_kernel_tool_budget.py` checks the wiring, not a refusal.

**T-09 · GAP · `TOOLS_ENABLED=false` does not stop a resumed confirmation.** (CODE)
The flag nulls `deps.tools`, but the resume path executes its stored plan through the executor, which still holds the registry (`container.py:456-461`; `kernel.py:473,783,1746`). Untested.

**T-10 · FIXED · MCP name collisions silently drop tools.** (CODE)

> **Fix:** `_parse_mcp_servers` and `McpSettings` (`config/settings.py`) refuse two MCP servers whose names sanitise to the same identifier (`My-Server` vs `my_server`) with `ConfigurationError`. `build_mcp_bridge_tools` (`tools/coding.py`) tracks `taken` tool names by their winning `McpToolDescriptor`, skips colliding or unusable MCP tool declarations without aborting discovery for the remaining tools, logs both sides of a collision (`collides_with_server`, `collides_with_name`), and populates an optional `collisions` list stored on `Container.mcp_tool_collisions` (`tests/unit/test_tools_review_engineer.py`).

Original finding:
- `My-Server` / `my_server`, or server `a` + tool `b_c` vs server `a_b` + tool `c`, sanitize to the same name.
- `register_all` raises on the duplicate, so tools before it stay registered and every later tool is lost. The log says only "MCP tool discovery failed".
- There is no length cap. Blueprint names cap at 48 because providers cap function names at 64.

**T-11 · FIXED · The client drops a proposed task's description.** (CODE)

> **Fix:** `PendingActionsExecutor._executeOne` (`pending_actions_executor.dart:107-114`) now passes `description: params['description']?.toString()` to `addTask`.

Original finding:
`pending_actions_executor.dart:101-109` never passes `params['description']` to `addTask`, although `AddTaskFn` accepts it (`:27-32`) and the backend sends it.

**T-12 · FIXED (live voice) · `propose_*` over live voice.** (CODE)

> **Fix:** `LiveServerFrame.tryParse` (`live_voice_protocol.dart`) now parses `actions` frames into `LiveActionsFrame`, `LiveVoiceViewModel._onFrame` forwards non-empty actions to `pendingActionsProvider`, and `LiveVoicePage` renders `PendingActionsCard` (`test/live_voice_protocol_test.dart`, `test/live_voice_view_model_test.dart`).

Original finding:
- The voice server forwards `actions` frames, but `live_voice_protocol.dart` has no `actions` case (`:111-159`), so they fall to `default`.
- `POST /api/v3/chat` has no `actions` field at all.

### P4

**T-13 · FIXED · Engineer tools are advertised when nothing can run them.** (RUN)

> **Fix:** `AIKernel._tool_declarations` (`kernel.py`) now hides `_REQUIRES_PAIRED_DESKTOP` (`docker_build`, `docker_compose`, `profile_python`) whenever the calling user has no paired desktop session, and hides `_REQUIRES_SANDBOX_OR_DESKTOP` (`write_file`, `write_files`, `edit_file`, `run_command`, `clone_repository`, `git_*`, `workspace_snapshot`/`list_workspace_snapshots`/`workspace_restore`, `lint_code`, `check_format`, `scan_security`) when neither a paired desktop nor a server-side sandbox runner (`sandbox_runner._fallback`) is available for the turn (`tests/unit/test_agents.py::test_sandbox_and_desktop_tools_are_hidden_when_their_runner_is_absent`).

Original finding:
- With no paired desktop and no Docker (Railway, today's production), the model is still offered `run_command`, `write_file` and the `git_*` tools, and every call fails.
- The container docstring chooses this deliberately ("registered-but-unusable is a better failure than invisible"). It contradicts the registry's own "don't advertise what always fails" rule and costs a planning generation per attempt.
- The error text is at least actionable.

**T-14 · GAP (by design) · There is no push path on deployments without Docker.** (RUN)
- `git_push` needs `host_git`, which needs the Docker workspace runner. On Railway it never exists, and the engineer grant names it regardless.
- A desktop user's only route to a push is `run_command "git push"` through daemon approval.

**T-15 · FIXED · Weak input validation; the in-memory store diverges from Postgres.** (RUN)

> **Fix:** `tools/personal_os.py` (`parse_iso`, `require_title`, `_later`, non-empty health metric check) and `tools/records.py` (`_checked`, `_event_order`, non-empty health metric check) now validate ISO-8601 dates/datetimes, non-empty titles, event ordering (`end >= start`), finite numbers, and NUL-byte stripping before emitting a proposal or writing to any store (`tests/unit/test_tools_review_product.py`). In addition, `WorkspaceManager.enforce_disk_quota` is now enforced via `sessions.write_handle` / `sessions.enforce_quota` before every workspace-writing tool (`write_file`, `edit_file`, `write_files`, `git_commit`, `git_checkout_branch`, `workspace_snapshot`, `workspace_restore`, `clone_repository`) while keeping `run_command` exempt so an over-quota workspace can still free space (`tests/unit/test_tools_review_engineer.py`).

Original finding:
- The schema validator enforces `format` only for `github_repo` (`schema.py:242`). No date or datetime parameter is checked.
- Observed: `create_calendar_event` stored `start_time="audit-probe"`, `create_health_record` stored an empty record, and `propose_event` emitted `startTime: "audit-probe"`.
- Production's `PostgresSyncedPersonalOsStore` rejects bad dates (`_parse_datetime`), so this is not live corruption. But the in-memory adapter, the substrate the whole default suite runs on, accepts what production refuses, so no test can catch a bad-date path.
- `propose_*` output is not stored by the server at all: the client's `DateTime.tryParse(...) ?? DateTime.now()` silently turns a bad `startTime` into "now".

**T-16 · FIXED on `main` by #241 · The `delegate_to_agent` target enum is frozen at container build.** (CODE; C-06)

> `delegate_to_agent` now reads its target enum from the live agent registry (`tools/delegation.py::_LiveTargetsTool`), so blueprint `bp_*` agents can be targets. This branch's name+prefix grant intersection (T-03) composes with it unchanged.

Original finding:
Blueprint agents (`bp_*`) can never be delegation targets.

**T-17 · FIXED · Routes with no client caller.** (CODE; C-07)

> **Fix:** `GET /api/v3/tools` is now called by `ToolCatalogRepository` (`aura_client/lib/features/chat/data/tool_catalog_repository.dart`), and `GET /api/v3/agents/runs/search` + `GET /api/v3/agents/runs/{run_id}` are called by `RunHistoryRepository` (`aura_client/lib/features/engineering/data/run_history_repository.dart`; see `docs/guides/api-reachability.md`).

Original finding:
`GET /api/v3/tools`, `/api/v3/agents/runs` and `/agents/runs/{id}` have no caller. This agrees with `docs/guides/api-reachability.md:76-77,129`.

**T-18 · GAP · Two naming conventions for the same entities.** (RUN)
`propose_task` takes `dueDate` / `startTime`; `create_task` / `create_calendar_event` take `due_date` / `start_time`. A planner switching between the two tool families has to switch conventions.

**T-19 · FIXED · `confirmation_invalid` has no client handling.** (CODE)

> **Fix:** `chat_repository.dart` maps `confirmation_invalid` (both SSE `error` frames and HTTP 409 `AuraApiError` responses) to `_SseSendOutcome.approvalExpired` and surfaces a dedicated expired-approval transcript row (`chat_connection_error.dart`, `test/chat_robustness_test.dart`), `AuraFailure._fromApiError` (`aura_failure.dart`) maps `confirmation_invalid` to localized `chatApprovalExpiredNotice` with `canRetry: false`, and `LiveVoiceViewModel._onFrame` (`live_voice_view_model.dart`) clears pending confirmation state, returns to `listening`, and shows `chatApprovalExpiredNotice` (`test/live_voice_view_model_test.dart`).

Original finding:
It arrives as a generic SSE `error`, with no prompt to re-ask.

## 4. Pass-C findings rechecked

| Pass C | Status | Evidence |
|---|---|---|
| C-01 taint not carried across delegation (P1) | ✅ **Fixed** | `delegation.py:144-147` passes `inherited_taint=context.tainted` |
| C-02 `write_files` missing from engineer grant | ✅ **Fixed** | `agents/registry.py:226`. MCP prefix grant fixed as **T-03**, and `test_tools_review_engine.py` pins every registered tool against persona grants |
| C-03 engineer tools reachable via orchestrator | ✅ **Mitigated** | **T-07**: orchestrator turns with `engineer_agent` now receive `engineer_loop_deadline_s` (`kernel.py::_tool_context`) |
| C-04 `remember_fact` granted to every persona | ✅ **Fixed** | split into `_MEMORY_WRITE_TOOLS`; `decision_agent` excluded (`registry.py:129-135,210`) |
| C-05 `max_calls_per_turn` never enforced | ✅ **Fixed** | **T-08** (`_turn_budget_and_gate`, parent-chained `RunBudgetGate`, `test_kernel_tool_budget.py`) |
| C-06 delegation enum frozen | ✅ **Fixed** (#241) | **T-16** |
| C-07 `GET /tools` has no caller | ✅ **Fixed** | **T-17** (`tool_catalog_repository.dart`, `run_history_repository.dart`) |
| C-08 opt-in tools have no wiring | ✅ **Fixed** | **T-06** (`AURA_TOOLS_FILES_ROOT`, `AURA_TOOLS_HTTP_ALLOWED_HOSTS`, `AURA_TOOLS_WEATHER_ENABLED`) |

## 5. Checked and fine

- **Client and server agree exactly** on the confirmation round-trip (SSE and voice), on all `propose_*` action types and keys, and on the daemon RPC surface.
- **Integer handling is safe.** The validator rejects `7.5` for an integer parameter and coerces `"450"` / `450.0` to `450` (RUN), so the client's `as int` casts on `log_health` are safe.
- **Blueprint and connector tools are read-only:** `side_effecting=False`; Postgres reads run in read-only transactions; OpenAPI is GET-only.
- **MCP discovery failure is contained:** one unreachable server is skipped and startup continues.
- **The calculator cannot be used for code execution or memory exhaustion:** AST allowlist, exponent and factorial bounds. `fetch_url`'s SSRF controls (allowlist, private-range and DNS-resolution checks, no redirects, streamed body cap) are sound, but unreachable (T-06).

## 6. Reproducing

```bash
cd aura_backend   # locked venv per CLAUDE.md
PYTHONPATH=. python ../docs/reports/audit/tools-audit-2026-11-16/probe_tools.py /tmp/probe.json
```

The probe writes every registered spec, the persona-grant cross-check, one call per tool and a CRUD chain to the JSON file.

**Install note:** with `uv pip`, `requirements.lock` needs `--index-strategy unsafe-best-match` to match pip's behaviour. Otherwise uv resolves `certifi` only from the PyTorch CPU index and fails. Hashes are still enforced.
