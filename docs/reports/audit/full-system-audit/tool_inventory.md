# Tool inventory — pass C

**Audit:** AURA AI full-system engineering audit · **Pass:** C (agents, personas, tool policy, delegation)
**Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (`ffbbb9f`) · **Date:** 2026-10-31
**Author:** Audit subagent pass C

The Findings, previous-finding recheck, verification log and documentation-inaccuracy sections for this pass live in `agent_inventory.md` (§4-§7) and `findings_C.json`; this file is the tool matrix those sections cite.

## 1. Scope and method

All rows are `CODE-READ` from `aura_backend/aura_core/tools/{builtin,personal_os,records,planning,coding,delegation,workspaces,registry,policy}.py`, `aura_core/container.py:1383-1413,1605-1625` and `aura_core/agents/registry.py`. Nothing was executed (see `agent_inventory.md` §6). "Registered when" is the container condition; "Operationally present in production" is `UNKNOWN` for every opt-in group because production config was not probed (brief rule 1).

Policy vocabulary (from `tools/policy.py:53-151` and `domain/tools.py:36-40,168-199`):
- **S** = sensitivity (`PUBLIC`<`PERSONAL`<`CONFIDENTIAL`<`SYSTEM`); **SE** = side-effecting; **WL** = `workspace_local` (tainted-context exemption, ADR-014; set pinned by `tests/architecture/test_workspace_local_tools.py:31-39`); **UC** = tagged `untrusted_content` (output taints the turn, `registry.py:454-466`); **Cap** = `required_capability`.
- **Confirmation** column is the untainted-context outcome of `ToolPolicy.evaluate` with defaults: SE ∧ S≥CONFIDENTIAL ∧ ¬WL ⇒ `requires_confirmation` (`domain/tools.py:168-199`, `policy.py:141-149`). In a tainted context every SE ∧ ¬WL tool needs confirmation (`policy.py:136-139`); WL tools routed to a paired desktop also need it when tainted (`policy.py:129-134`).

## 2. Registered tools

### 2a. Always registered (`builtin.py:605-608`)

| Tool | S | SE | WL | UC | Cap | Confirmation (untainted) | Personas holding it | Evidence |
|---|---|---|---|---|---|---|---|---|
| `calculator` | PUBLIC | no | – | – | – | none | orchestrator, finance_advisor, research_agent, coding_agent, decision_agent, engineer | `builtin.py:133-164` |
| `current_datetime` | PUBLIC | no | – | – | – | none | all (`_READ_TOOLS`) | `builtin.py:167-183` |
| `prioritize_tasks` | PERSONAL | no | – | – | – | none | orchestrator, task_planner | `planning.py:107-153` |
| `find_calendar_slots` | PERSONAL | no | – | – | – | none | orchestrator, task_planner | `planning.py:215-305` |
| `propose_task` | PERSONAL | yes | – | – | – | none untainted; confirm when tainted; client applies after approval card | orchestrator, task_planner | `personal_os.py:160-191` |
| `propose_event` | PERSONAL | yes | – | – | – | as above | orchestrator, task_planner | `personal_os.py:193-218` |
| `propose_expense` | PERSONAL | yes | – | – | – | as above | orchestrator, finance_advisor | `personal_os.py:220-242` |
| `propose_income` | PERSONAL | yes | – | – | – | as above | orchestrator, finance_advisor | `personal_os.py:244-266` |
| `propose_health_log` | PERSONAL | yes | – | – | – | as above | orchestrator, health_companion | `personal_os.py:268-297` |
| `delegate_to_agent` | SYSTEM | **no** | – | yes | – | none (deliberately, `delegation.py:86-91`) | orchestrator only (SYSTEM ceiling + unrestricted grant); registered after kernel at `container.py:605-614` | `delegation.py:66-96` |

### 2b. Registered when `knowledge`/`memory` engines exist (always in the default container)

| Tool | S | SE | WL | UC | Cap | Confirmation | Personas | Evidence |
|---|---|---|---|---|---|---|---|---|
| `search_knowledge` | PERSONAL | no | – | – | – | none | all | `builtin.py:199-231` |
| `recall_memory` | PERSONAL | no | – | – | – | none; scope-filtered by persona | all | `builtin.py:242-267` |
| `remember_fact` | PERSONAL | **yes** | – | – | – | none untainted | all (via `_READ_TOOLS`, finding C-04) | `builtin.py:269-291` |

### 2c. Registered when `personal_os_store` is wired (`builtin.py:613-614`; container always passes it, `container.py:1403`)

Four domains × (create, list, update, delete) = 16 tools from `records.py:160-217`. All `PERSONAL`; create/update/delete are SE, list is not; none WL/UC; **no confirmation gate of their own when untainted** (`records.py:10-14`).

| Domain | Tools | Personas |
|---|---|---|
| task | `create_task`, `list_tasks`, `update_task`, `delete_task` | orchestrator, task_planner |
| calendar_event | `create_calendar_event`, `list_calendar_events`, `update_calendar_event`, `delete_calendar_event` | orchestrator, task_planner |
| finance_record | `create_finance_record`, `list_finance_records`, `update_finance_record`, `delete_finance_record` | orchestrator, finance_advisor |
| health_record | `create_health_record`, `list_health_records`, `update_health_record`, `delete_health_record` | orchestrator, health_companion |

Production reachability of the backing store (migration `0019`, data move) is a pass-B/D question; `docs/status/BACKEND_STATUS.md:80-84,188-189` says it was pending as of 2026-10-31 — `DOCUMENTED ONLY`.

### 2d. Opt-in by configuration

| Tool | Registered when | S | SE | WL | UC | Cap | Confirmation | Personas | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `list_files`, `read_file` | `workspace=` passed to `default_tools` — **container never passes it** (`container.py:1400-1412`) | PERSONAL | no | – | – | – | none | — (not in any explicit grant; orchestrator only if ever registered) | `builtin.py:301-376` — IMPLEMENTED BUT UNREACHABLE via container wiring |
| `fetch_url` | `AURA_HTTP_ALLOWED_HOSTS` non-empty — **container never passes `allowed_http_hosts`** (`container.py:1400-1412`) | PUBLIC | no | – | yes | – | none | research_agent (grant names it), orchestrator | `builtin.py:442-503` — IMPLEMENTED BUT UNREACHABLE via container wiring (persona comment at `registry.py:186-187` assumes it can be enabled) |
| `get_weather` | `enable_weather=True` — **never passed by container** | PUBLIC | no | – | – | – | none | orchestrator | `builtin.py:506-562` — IMPLEMENTED BUT UNREACHABLE |

### 2e. Engineer tools (`required_capability="engineer_agent"`, `coding.py:22`), registered because `sandbox_runner` is always a `CompositeSandboxRunner` (`container.py:1415-1426`)

| Tool | S | SE | WL | UC | Confirmation (untainted) | In `engineer` grant? | Evidence |
|---|---|---|---|---|---|---|---|
| `write_file` | PERSONAL | yes | yes | – | none (WL) | yes | `coding.py:470-482` |
| `edit_file` | PERSONAL | yes | yes | – | none (WL) | yes | `coding.py:491-503` |
| `write_files` | PERSONAL | yes | yes | – | none (WL) | **no** (C-02) | `coding.py:520-538` |
| `run_command` | SYSTEM | yes | yes | yes | none (WL); confirm when tainted and host-routed | yes | `coding.py:566-585` |
| `git_diff` | PERSONAL | no | – | – | none | yes | `coding.py:612-617` |
| `git_commit` | PERSONAL | yes | yes | – | none (WL) | yes | `coding.py:626-633` |
| `git_checkout_branch` | PERSONAL | yes | yes | – | none (WL) | yes | `coding.py:647-654` |
| `open_workspace` | PERSONAL | yes | yes | – | none (WL); hidden when desktop paired (`kernel.py:1444,1462-1467`); needs `workspace_manager` | yes | `coding.py:668-679` |
| `list_workspaces` | PERSONAL | no | – | – | none; hidden when desktop paired | yes | `coding.py:699-706` |
| `destroy_workspace` | CONFIDENTIAL | yes | no | – | **confirm** | yes | `coding.py:721-734` |
| `git_status` | PERSONAL | no | – | – | none | yes | `coding.py:750-755` |
| `git_push` | CONFIDENTIAL | yes | no | – | **confirm** | yes | `coding.py:765-780` |
| `clone_repository` | CONFIDENTIAL | yes | no | yes | **confirm**; needs `github_client_resolver` | yes | `coding.py:838-860` |
| `github_list_repository_files` | CONFIDENTIAL | no | – | yes | none | yes | `coding.py:1044-1061` |
| `github_get_file_contents` | CONFIDENTIAL | no | – | yes | none | yes | `coding.py:1087-1099` |
| `github_get_issue` | CONFIDENTIAL | no | – | yes | none | yes | `coding.py:1107-1117` |
| `github_get_pull_request` | CONFIDENTIAL | no | – | yes | none | yes | `coding.py:1123-1133` |
| `github_create_branch` | CONFIDENTIAL | yes | no | – | **confirm** | yes | `coding.py:1141-1150` |
| `github_commit_files` | CONFIDENTIAL | yes | no | – | **confirm** | yes | `coding.py:1207-1217` |
| `github_create_draft_pull_request` | CONFIDENTIAL | yes | no | – | **confirm** | yes | `coding.py:1230-1242` |

`github_*` tools register only when `github_client_resolver` is not `None` (`builtin.py:634-635`); whether a resolver exists in production is `UNKNOWN`.

### 2f. MCP-bridged tools (`coding.py:1295-1339`; registered at `container.py:1611-1625` when `mcp_client` is configured)

| Name pattern | Default | If listed in `AURA_MCP_SAFE_TOOLS` | Cap | In `engineer` grant? |
|---|---|---|---|---|
| `mcp_{server}_{tool}` | CONFIDENTIAL, SE, UC ⇒ always confirm | PERSONAL, not SE, not UC ⇒ runs freely, no taint | `engineer_agent` | **no** — `registry.py:213-225` names none; only an `engineer_agent` caller on `orchestrator` can reach them (C-02/C-03) |

Operational presence: `UNKNOWN` (no MCP config read in this pass).

## 3. Cross-cutting observations (feed `agent_inventory.md` §4)

1. Three opt-in tool groups (`list_files`/`read_file`, `fetch_url`, `get_weather`) have builder code and a settings story but **no container wiring** (`container.py:1400-1412` never passes `workspace`, `allowed_http_hosts`, or `enable_weather`). `research_agent`'s only network tool therefore cannot be enabled by configuration at this baseline — IMPLEMENTED BUT UNREACHABLE (recorded under C-02's family; see `findings_C.json` id C-08).
2. The 16 personal-OS CRUD tools and `remember_fact` are the only side-effecting tools that run with **no** confirmation when untainted; everything else is either WL (sandbox-confined) or CONFIDENTIAL+ (always confirmed). That makes taint propagation (C-01) the single control standing between injected content and a durable write.
3. `execute_many` and the execution engine defer taint-gated calls behind tainting calls in the same batch (`registry.py:498-519`) — consistent with M4; test referenced at `registry.py:465` not run.
4. Output size: every tool has `max_result_chars` truncation with a visible marker (`registry.py:422-452`); structured MCP results are list-trimmed rather than cut mid-JSON (`registry.py:622-666`).
