# Agent inventory and agent/tool-policy findings — pass C

**Audit:** AURA AI full-system engineering audit · **Pass:** C (agents, personas, tool policy, delegation)
**Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (`ffbbb9f`) · **Date:** 2026-10-31
**Author:** Audit subagent pass C

Companion files: `tool_inventory.md` (every registered tool, its policy class and reachability) and `findings_C.json` (sidecar mirroring the Findings table below).

## 1. Scope and method

Read (all `CODE-READ`, with `Read`/`Grep`/`Glob` only — see §6 for why nothing was executed):

- `aura_backend/aura_core/agents/registry.py` (whole file), `aura_core/tools/{registry,policy,delegation,builtin,personal_os,records,planning,workspaces}.py` (whole files), `aura_core/tools/coding.py` (tool declarations via grep + lines 1280-1362), `aura_core/kernel/kernel.py` (whole file), `aura_core/prompts/library.py` (whole file), `aura_core/safety/{pipeline,guards}.py` (whole files), `aura_core/container.py` (596-625, 1380-1430, 1605-1630), `aura_core/api/v3.py` (route map, 405-475, 1315-1365), `aura_core/domain/{tools,memory}.py` (grep), `aura_core/memory/engine.py` (grep), `aura_core/execution/agentic_loop.py` (grep), `aura_core/adaptation/runtime.py` (grep), `aura_core/config/settings.py` (`ToolSettings`, `engineer_agent_roles`).
- Tests located, not run: `tests/unit/test_agents.py`, `tests/unit/test_delegation.py`, `tests/unit/test_engineer_tools.py`, `tests/architecture/test_workspace_local_tools.py`, `tests/certification/test_agent_containment.py`, `tests/unit/test_agentic_loop.py`.
- Prior material: `docs/production/gap_register.yaml` (GAP-024), `docs/reports/AURA_FULL_SYSTEM_REVIEW.md` (§ agents, lines 207-209, 632), `docs/status/BACKEND_STATUS.md` (grep for agent/persona).
- Client reachability: `aura_client/lib` grep for `/api/v3/agents` and `/api/v3/tools`.

Not done: no `pytest`, no `python -c` import, no production probe (coordinator instruction: C: temp drive is full, Bash output unreliable). Every "tested" claim below is therefore `CODE-PRESENT / BEHAVIOR UNVERIFIED` unless stated otherwise.

## 2. How an "agent" works (the mechanism the inventory is measured against)

| Mechanism | Where | Evidence |
|---|---|---|
| Persona = `AgentSpec` data: prompt id, `allowed_tools`, `required_capabilities`, `risk_ceiling`, `memory_scope`, loop budgets | `aura_core/agents/registry.py:31-82` | CODE-READ |
| Resolution: unknown id → orchestrator; missing `required_capabilities` → **silent** fallback to orchestrator (surfaced as `TurnResult.resolved_agent` / `done.resolved_agent`) | `registry.py:97-113`; `kernel/kernel.py:191-195, 366-367, 615, 991` | CODE-READ |
| Tool advertisement = registry `available()` (capability + sensitivity) ∩ persona `allowed_tools`, minus desktop-only tools when a hostd session is paired | `tools/registry.py:169-204`; `kernel/kernel.py:1452-1473` | CODE-READ |
| Dispatch policy (ordered): denylist/allowlist → `outside_agent_grant` → sensitivity ceiling → `missing_capability` → confirmed → workspace-local exemption → taint → `requires_confirmation` → confidential side-effect | `tools/policy.py:53-151` | CODE-READ |
| Taint sources: quarantined retrieval chunk, any `caller_context`, resumed tainted turn, any tool tagged `untrusted_content` (`fetch_url`, `run_command`, `clone_repository`, `github_get_*`, non-safe MCP, `delegate_to_agent`) | `kernel.py:399-426`; `tools/registry.py:454-466`; `builtin.py:467`; `coding.py:585,860,1061-1133,1328`; `delegation.py:95` | CODE-READ |
| Memory partition: persona reads own scope + `shared`; writes to own scope (`remember_fact`, kernel recall) | `domain/memory.py:39-58`; `kernel.py:1056-1057,1073`; `builtin.py:258,289`; `memory/engine.py:165,271` | CODE-READ |
| Loop: every persona runs `AgenticExecutor`; `max_iterations=0` for single-pass personas, 8 for `engineer`; tool/risk budgets gated pre-dispatch | `registry.py:67-82`; `kernel.py:1564-1598`; `execution/agentic_loop.py:111-138,206-217` | CODE-READ |
| Capability `engineer_agent` is granted by role via `ENGINEER_AGENT_ROLES` (empty by default) or a blueprint binding | `config/settings.py:1014-1021,1057`; `api/v3.py:416-421` | CODE-READ |
| Delegation: `delegate_to_agent` (orchestrator only, by grant shape) recurses `AIKernel.run()` with intersected grant, depth ≤ `max_delegation_depth` (default 1) | `tools/delegation.py:44-161`; `container.py:605-614` | CODE-READ |
| Blueprint-compiled agents (Track AP) are appended to a snapshot `AgentRegistry` at runtime with prefixed memory scopes | `adaptation/runtime.py:154-162,184-216` | CODE-PRESENT / BEHAVIOR UNVERIFIED |

## 3. Agent inventory

Sensitivity order: `PUBLIC(0) < PERSONAL(1) < CONFIDENTIAL(2) < SYSTEM(3)` (`domain/tools.py:36-40`). `_READ_TOOLS` = `current_datetime, search_knowledge, recall_memory, remember_fact` (`registry.py:129`).

| id | Prompt (version) | `allowed_tools` | Risk ceiling | Required capability | Memory scope | Loop | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| `orchestrator` | `system.orchestrator` 1.1.0 | **None** = everything the caller's capabilities allow, incl. `delegate_to_agent` and (for an `engineer_agent` caller) every engineer tool | SYSTEM | — | shared | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED | `registry.py:144-148`; `library.py:38-58` |
| `task_planner` | `system.task_planner` 1.1.0 | `_READ_TOOLS` + `propose_task`, `propose_event`, `create/list/update/delete_task`, `create/list/update/delete_calendar_event`, `prioritize_tasks`, `find_calendar_slots` | PERSONAL | — | shared | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED | `registry.py:149-163` |
| `finance_advisor` | `system.finance_advisor` 1.1.0 | `_READ_TOOLS` + `calculator`, `propose_expense`, `propose_income`, `create/list/update/delete_finance_record` | PERSONAL | — | `finance` | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED | `registry.py:164-173` |
| `health_companion` | `system.health_companion` 1.1.0 | `_READ_TOOLS` + `propose_health_log`, `create/list/update/delete_health_record` | PERSONAL | — | `health` | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED | `registry.py:174-182` |
| `research_agent` | `system.research_agent` 1.1.0 | `_READ_TOOLS` + `calculator`, `fetch_url` (only registered when `AURA_HTTP_ALLOWED_HOSTS` set) | CONFIDENTIAL | — | shared | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED | `registry.py:183-190`; `builtin.py:449-451` |
| `coding_agent` | `system.coding_agent` 1.1.0 | `_READ_TOOLS` + `calculator` (no file/repo tools) | PERSONAL | — | shared | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED (prompt promises code review; grant has no code-reaching tool) | `registry.py:191-196`; `library.py:162-179` |
| `decision_agent` | `system.decision_agent` 1.1.0 | `_READ_TOOLS` + `calculator` ("read-only" per comment, but `remember_fact` is a write — C-04) | PERSONAL | — | shared | 1 pass | IMPLEMENTED — NOT FULLY VERIFIED | `registry.py:197-205` |
| `engineer` | `system.engineer` 1.3.0 (two-branch: sandbox vs desktop) | `_READ_TOOLS` + `calculator`, `write_file`, `edit_file`, `run_command`, `clone_repository`, `git_diff/commit/checkout_branch/status/push`, `open/list/destroy_workspace`, `github_list_repository_files`, `github_get_file_contents`, `github_get_issue`, `github_get_pull_request`, `github_create_branch`, `github_commit_files`, `github_create_draft_pull_request` — **not** `write_files`, not any `mcp_*` (C-02) | SYSTEM | `engineer_agent` | `engineer` | 8 replans, `tool_budget=216`, `risk_budget=864` | IMPLEMENTED — NOT FULLY VERIFIED (sandbox path is ADR-013/014 "built, not proven") | `registry.py:206-243`; `library.py:180-231` |
| blueprint-compiled agents | `system.blueprint_agent` 1.0.0 | per blueprint grant | per blueprint | per blueprint binding | `generated:…` prefix | — | CODE-PRESENT / BEHAVIOR UNVERIFIED | `adaptation/runtime.py:154-162`; `library.py:138-161` |

Count: **8 built-in personas** (registry docstring at `registry.py:133` still says "seven" — see §7).

Client reachability: `GET /api/v3/agents` is called by `aura_client/lib/features/chat/data/agents_repository.dart:94` (reachable). `GET /api/v3/tools` has **no** caller under `aura_client/lib` (grep, 0 hits) — IMPLEMENTED BUT UNREACHABLE from the shipped client.

### 3a. Persona isolation matrix (what actually separates personas)

| Boundary | Enforced? | Where | Note |
|---|---|---|---|
| Tool set | Yes, at advertisement and at dispatch | `kernel.py:1468-1473`; `policy.py:86-87` | Orchestrator is unrestricted by design |
| Risk ceiling | Yes, both places | `kernel.py:1457-1461`; `policy.py:89-94` | |
| Persona selection | Weak: capability gate only on `engineer`; any caller may pick any other persona | `registry.py:101-113` | Fallback is silent except for `resolved_agent` |
| Memory | Yes (scope filter + write scope); only `finance`/`health`/`engineer` have private scopes | `memory/engine.py:165,271` | No dedicated scope-isolation test located (test names in `tests/unit/test_agents.py:24-272` contain no scope test) — exit condition of GAP-024 not evidenced |
| Conversation | Delegated sub-turn is stateless (`session_id=None`) | `delegation.py:137` | |
| Taint | **Not propagated across delegation** | `delegation.py:134-148`; `kernel.py:111-170` | Finding C-01 |

## 4. Findings

| Local ID | Severity | Title | Subsystem | Evidence (path:line / command) | Current behavior | Expected behavior | Recommended action | Confidence |
|---|---|---|---|---|---|---|---|---|
| C-01 | P1 | Taint is not carried across `delegate_to_agent`, so a tainted orchestrator turn can trigger unconfirmed server-side writes through a sub-persona | tools/delegation, kernel, tool policy | `aura_core/tools/delegation.py:84-96` (tool is not `side_effecting`, so `policy.py:136-139` never holds it in a tainted context), `delegation.py:134-148` (child `TurnRequest` has no taint input), `aura_core/kernel/kernel.py:111-170` (no taint field on `TurnRequest`), `kernel.py:399-426` (child taint computed only from the child's own retrieval/caller_context/resume), `aura_core/tools/records.py:10-14` (`create_*/update_*/delete_*` are side-effecting PERSONAL with no confirmation gate when untainted), `aura_core/agents/registry.py:155-181` (`task_planner`/`finance_advisor`/`health_companion` grants include those CRUD tools), `aura_core/container.py:1400-1403` (CRUD tools registered whenever `personal_os_store` is wired). Test gap: `grep taint tests/unit/test_delegation.py` → 0 hits. | Parent turn becomes tainted (quarantined chunk, `fetch_url`, caller context). `delegate_to_agent` is allowed (`policy_ok`). The child turn starts untainted; a planned `create_task`/`delete_finance_record` etc. in the child runs immediately. The tool docstring (`delegation.py:70-73`) only covers sub-tasks that *would* need confirmation, which an untainted CRUD call does not. | ADR-006: a turn whose context was quarantined cannot run a side-effecting tool without confirmation, regardless of how many kernel hops are involved. | Add an explicit taint input to `TurnRequest` (e.g. `inherited_taint: bool`), set it from `context.tainted` in `delegate_to_agent`, OR it into the child's `tainted` at `kernel.py:426/734`, and add a test in `tests/unit/test_delegation.py` that a tainted parent context makes the child's side-effecting call return `confirmation_required`. Alternatively mark `delegate_to_agent` `side_effecting=True` with `workspace_local`-style exemption only when the child reports no side effects. | High (code), Med (practical exploitability: needs the planner model to follow injected text and a persona with CRUD tools) |
| C-02 | P2 | `write_files` is registered for the engineer capability but absent from the `engineer` persona's grant, so the persona can never use it | agents/registry, tools/coding | `aura_core/tools/coding.py:520-538,828` (registered, `required_capability="engineer_agent"`), `aura_core/agents/registry.py:213-225` (grant lists `write_file`, `edit_file` but not `write_files`), `aura_core/kernel/kernel.py:1468-1473` (filtered out of declarations), `aura_core/tools/policy.py:86-87` (denied `outside_agent_grant` if called anyway). `tests/architecture/test_workspace_local_tools.py:34` and `tests/unit/test_engineer_tools.py:149-210` exercise the tool directly with a hand-built `ToolContext`, bypassing the persona grant. | The D6 atomic multi-file write is dead for the only persona it was built for; it is reachable only by an `engineer_agent`-capable caller who selects `orchestrator` (unrestricted grant). | Every tool built for the engineer is in the engineer's grant, and a test pins that the grant covers the registered engineer tool names. | Add `write_files` to the grant at `registry.py:213-225`; add a test asserting `set(engineer.allowed_tools) ⊇ {t.spec.name for t in build_sandbox_tools(...)+build_github_tools(...)}`. Same check should cover `mcp_*` names if MCP tools are meant to be engineer-usable (today they are not in the grant either — `registry.py:209-212` only says "any MCP-bridged tool" in a comment). | High |
| C-03 | P3 | Engineer tooling is reachable under the `orchestrator` persona without the engineer prompt or loop | agents/registry, kernel | `aura_core/agents/registry.py:144-148` (`allowed_tools=None`, SYSTEM ceiling), `registry.py:226-232` (comment calls the persona+tool gate "belt-and-braces"), `aura_core/kernel/kernel.py:1452-1473`, `aura_core/tools/registry.py:169-192`. | A caller with `engineer_agent` who sends `agent="orchestrator"` (the client default, `api/v3.py:217`) is offered `run_command`, `git_push`, `github_commit_files` etc. with the generic orchestrator prompt (`library.py:38-58`), no "treat command output as data" instruction, and `max_plan_iterations=1`. Tool-level policy (capability, confirmation) still applies, so this is a prompt/UX gap, not a privilege escalation. | A persona gate that is described as a second layer should actually constrain which prompt/loop the engineer tools run under. | Either give `orchestrator` an explicit `allowed_tools` that excludes `required_capability` tools, or document that the gate is tool-level only and adjust the `registry.py:226-232` comment. | High |
| C-04 | P3 | `_READ_TOOLS` grants `remember_fact` (a durable write) to every persona, including `decision_agent` described as read-only | agents/registry | `aura_core/agents/registry.py:129` (`_READ_TOOLS` includes `remember_fact`), `registry.py:200-204` (comment: "Read-only and deliberately so"), `aura_core/tools/builtin.py:269-281` (`side_effecting=True`, PERSONAL). | Every persona can write long-term memory into its scope without confirmation when untainted. | The grant named `_READ_TOOLS` contains only reads; a persona documented as read-only cannot write. | Move `remember_fact` out of `_READ_TOOLS` and add it only to personas that should persist facts; fix the comment. | High |
| C-05 | P3 | `TOOLS_MAX_CALLS_PER_TURN` is parsed and documented but never enforced | config, execution | `aura_core/config/settings.py:905,916` are the only references (`grep max_calls_per_turn aura_core` → 2 hits, both in settings). Per-turn bounds that do exist are the execution engine's step cap and the agent `tool_budget` (`agentic_loop.py:111-138`). | An operator setting the variable changes nothing. | A setting either governs behaviour or does not exist. | Remove the setting or wire it into `ExecutionEngine`/`RunBudgetGate` as a default `tool_budget` for personas that set none. | High |
| C-06 | P4 | `delegate_to_agent`'s `target_agent_id` enum is frozen at container build, so blueprint-compiled agents are never delegable | tools/delegation, adaptation | `aura_core/tools/delegation.py:64,74-77` (enum computed once from `agents` at build time), `aura_core/adaptation/runtime.py:184-216` (generated agents appear only in the snapshot registry at runtime), `aura_core/container.py:605-614`. | Delegation targets are the 7 built-in non-orchestrator personas regardless of deployed blueprints. | Either documented as built-in-only, or the enum/validation reads the live registry. | Document, or validate `target_agent_id` against `agents.resolve()` at call time instead of a static enum. | High |
| C-07 | P4 | Agent-introspection endpoint `/api/v3/tools` has no client caller | api/v3, client | `aura_core/api/v3.py:1317-1334`; `grep "/api/v3/tools" aura_client/lib` → 0 hits (while `/api/v3/agents` is used at `aura_client/lib/features/chat/data/agents_repository.dart:94`). | Endpoint exists; nothing in the shipped client reads it. | Per CLAUDE.md's own warning, shipped endpoints should have a client or be marked server/admin-only. | Mark as admin/introspection-only in the route docstring or wire a client use. | High |

| C-08 | P3 | `fetch_url`, `list_files`/`read_file` and `get_weather` have builder code but no container wiring, so `research_agent`'s only network tool cannot be enabled by configuration | tools/builtin, container | `aura_core/tools/builtin.py:570-636` (`default_tools` accepts `workspace`, `allowed_http_hosts`, `enable_weather`), `aura_core/container.py:1400-1412` (none of the three is passed), `aura_core/agents/registry.py:186-189` (grant names `fetch_url`; comment assumes `AURA_HTTP_ALLOWED_HOSTS` enables it). | The three groups are IMPLEMENTED BUT UNREACHABLE at this baseline; `research_agent` has no retrieval beyond `search_knowledge`/`recall_memory`. | A persona grant naming a tool corresponds to a tool an operator can enable. | Wire the three parameters from settings in `Container._build_tools`, or drop `fetch_url` from the `research_agent` grant and the dead builders from `default_tools`. | High (wiring mismatch); production config not probed |

Deduplication note: the GAP-024 "every persona uses shared memory" claim is STALE (see §7) rather than a new finding. MCP default-deny (`coding.py:1295-1330`) and `fetch_url`/`run_command` taint tagging (`builtin.py:467`, `coding.py:585`) were reviewed and found consistent with ADR-006; no finding.

## 5. Previous-finding recheck

| Prior item | Source | Status at `ffbbb9f` | Evidence |
|---|---|---|---|
| "Agents are prompts, not agents" / shared tool universe across personas | `AURA_FULL_SYSTEM_REVIEW.md` §agents (rows 207-209); `registry.py:3-7` docstring | fixed-in-source-not-operationally-verified | `registry.py:132-245` grants; `policy.py:86-87`; `kernel.py:1468-1473`; tests present (`tests/unit/test_agents.py:147-272`) but not run here |
| GAP-024 agents/autonomy-isolation (`status: built_not_proven`) | `docs/production/gap_register.yaml:652-669` | partially fixed; `current_state` text STALE | Scopes `finance`/`health`/`engineer` exist (`registry.py:172,181,242`); budgets enforced (`agentic_loop.py:111-138`); exit condition "isolation tests" — no scope test located in `tests/unit/test_agents.py` (test names at :24-272) → cannot verify |
| SEC-07 (closures dispatched by if/elif, no validation/authorisation/audit) | `tools/registry.py:3-6` | fixed-in-source-not-operationally-verified | `registry.py:275-358` (resolve, validate, policy, audit) |
| M4: `fetch_url` output not scanned, could auto-approve later PERSONAL writes | `builtin.py:462-467` | fixed-in-source-not-operationally-verified | `registry.py:454-466` taints context; `execute_many` ordering `registry.py:498-519`; `tests/security/test_concurrent_taint.py` referenced at `registry.py:465` (existence not checked) |
| Agent capability isolation (brief's standing concern) | AUDIT_BRIEF §prior concerns | partially fixed | Capability gate exists only for `engineer`; C-01 shows taint does not follow a delegated grant |
| Memory isolation (brief's standing concern) | AUDIT_BRIEF | fixed-in-source-not-operationally-verified | `memory/engine.py:165,271`; `kernel.py:1056-1073` |
| F-xx items in `docs/status/BACKEND_STATUS.md` touching agents | `BACKEND_STATUS.md` grep "agent/persona" → only personal-OS data-move notes (:80-84,:188-189), no agent-registry item | not applicable | — |

## 6. Verification log

| Action | Result |
|---|---|
| `Read` of every file listed in §1 | Completed; all citations in this report are from those reads |
| `Grep engineer_agent aura_core` | 9 hits; capability is issued only via `ENGINEER_AGENT_ROLES` (`settings.py:1014-1021,1057`) or blueprint binding (`v3.py:416-421`) |
| `Grep write_files aura_backend --glob *.py` | Registered at `coding.py:521`, listed in `coding.py:828`, tested directly in `tests/unit/test_engineer_tools.py`; absent from `agents/registry.py` |
| `Grep taint tests/unit/test_delegation.py` | 0 hits |
| `Grep max_calls_per_turn aura_core` | 2 hits, both `settings.py` |
| `Grep "/api/v3/agents" and "/api/v3/tools" aura_client/lib` | `/agents` used (`agents_repository.dart:94`); `/tools` unused |
| `Glob tests/**/test_*agent*.py`, `test_*deleg*.py` | 8 + 1 files located (names only) |
| `pytest tests/unit/test_agents.py tests/unit/test_delegation.py` | **NOT RUNNABLE** in this pass: coordinator instruction (C: temp volume full; Bash output unreliable; nothing may be written to C:). Both suites are `CODE-PRESENT / BEHAVIOR UNVERIFIED` |
| Production probe of `/api/v3/agents` | Not performed (brief rule 1) |

## 7. Documentation inaccuracies

| Claim | Where | Code | Label |
|---|---|---|---|
| "The seven personas `AGENT_PROMPT_IDS` used to define" | `aura_core/agents/registry.py:133` | 8 entries at `registry.py:142-245`; `AGENT_PROMPT_IDS` has 8 keys at `prompts/library.py:514-523` | STALE |
| "Every persona uses memory_scope=\"shared\" … no per-agent memory isolation is exercised" | `docs/production/gap_register.yaml:656-658` | `finance`, `health`, `engineer` scopes at `registry.py:172,181,242` | STALE (already noted at `AURA_FULL_SYSTEM_REVIEW.md:209,632`; the register text was never updated) |
| `/api/v3/agents` is backed by `AGENT_PROMPT_IDS` | `aura_client/lib/features/chat/data/agents_repository.dart:9` | Backed by `container.agents` (`AgentRegistry`) with grant/ceiling/`usable` fields, `api/v3.py:1336-1364` | STALE |
| `decision_agent` is "Read-only and deliberately so" | `registry.py:200-202` | Grant includes `remember_fact` (write), `registry.py:129,203` | STALE (C-04) |
| Engineer grant covers "any MCP-bridged tool" | `registry.py:209-212` comment | Grant set at `registry.py:213-225` names no `mcp_*` tool; `kernel.py:1468-1473` would filter them out | STALE (C-02) |
| `TOOLS_MAX_CALLS_PER_TURN` governs tool calls per turn | `settings.py:905,916` | No consumer | STALE (C-05) |
| `delegate_to_agent` refuses any sub-task "whose plan would need the user's own confirmation" | `delegation.py:70-73` | Only `needs_confirmation`/`pending_tool_calls` are checked (`delegation.py:152-158`); untainted CRUD writes need none | Misleading (C-01) |
