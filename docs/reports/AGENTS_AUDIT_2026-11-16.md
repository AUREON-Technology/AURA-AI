# Agents audit — 2026-11-16

Scope: every agent and persona in the repo, how each is selected and enforced, its test and
evaluation coverage, and whether anything shows it working live. `main` at `ea04ab9`.

## How it was checked

- Code read: `aura_core/agents/registry.py`, `tools/delegation.py`, `kernel/kernel.py`
  (`_resolve_agent`, memory scopes), `container.py` (registry, delegation, sandbox and relay
  wiring), `adaptation/runtime.py` and `factories/agents.py` (Track AP generated agents),
  `prompts/library.py`, `api/v3.py` (`GET /agents`). Client: `features/chat/data/agents_repository.dart`,
  `persona_picker.dart`, `desktop/agent/agent_panel.dart`, `core/ai/orchestrator/`.
- Backend tests run locally in the locked environment (Python 3.11, both lockfiles, hashes checked):
  agent, delegation, agentic-loop, engineer, host-agent, host-relay, sandbox, personal-OS-tool
  suites, the two agent eval gates (`tests/e2e/test_agent_{tasks,runs}_gate.py`) and
  `tests/architecture`: **651 passed, 7 skipped**. The 7 skips are the Postgres-backed agent-run
  store. Run against a throwaway PostgreSQL 16, `test_agent_run_store.py` and
  `test_engineer_runs.py` gave **25 passed**.
- Host daemon (`aura_hostd`), Dart 3.12.2 (the CI pin): `dart analyze --fatal-infos` clean,
  `dart test` **274 passed** on Linux, once `debugpy` was installed, as `hostd-ci.yml` does. Without
  it, the debug-console test fails; that failure comes from the environment, not the code.
- Live: `https://aureon-ai.tech/health/live`, `/health/ready` and `/api/v3/agents` on 2026-11-16.

All of this is local evidence. No CI run backs it, and nothing here proves an agent live.

## Inventory and status

| Agent | Where | Grant (tools / risk / memory) | Status |
|---|---|---|---|
| `orchestrator` | `agents/registry.py` | all of the caller's tools; SYSTEM; shared. The only holder of `delegate_to_agent` | Built, tested |
| `task_planner` | registry | tasks/calendar CRUD + propose; PERSONAL; shared | Built, tested; its tools write the synced tables (CB-28), which are **not deployed** |
| `finance_advisor` | registry | finance CRUD + propose, calculator; PERSONAL; `finance` | Built, tested; same CB-28 caveat |
| `health_companion` | registry | health CRUD + propose; PERSONAL; `health` | Built, tested; same CB-28 caveat |
| `research_agent` | registry | read tools, calculator, `fetch_url`; CONFIDENTIAL; shared | Built, tested; `fetch_url` exists only with `AURA_HTTP_ALLOWED_HOSTS` |
| `coding_agent` | registry | read tools, calculator; PERSONAL; shared | Built; **prompt-only**, it has no code tools (F10) |
| `decision_agent` | registry | read tools, calculator (no writes, FSA-017); PERSONAL; shared | Built, tested |
| `engineer` | registry + `tools/coding.py` | sandbox FS/command/git, workspaces, GitHub r/w, MCP; SYSTEM; `engineer`; needs `engineer_agent`; 8 replans, tool budget 216 | **Built, not proven** (GAP-025, GAP-026) |
| Generated `bp_*` agents | `adaptation/` (Track AP7) | exact compiled-tool list; 2 replans, tool budget 8; own scope | Built, not proven |
| `delegate_to_agent` | `tools/delegation.py` | grant intersection; depth 1; 180 s | Built, tested |
| `AgenticRAG` | `rag/agentic.py` | a retrieval strategy, not a persona | Built, tested |
| `aura_hostd` | `aura_hostd/` | desktop half of the Engineer | Built, tested locally (274/274); no end-to-end report |
| `engineering2` ("Engineer 2.0") | `aura_core/engineering2/` | — | **Orphaned**: 919 LOC, 0 callers, 0 tests (F4) |
| `proactive` | `aura_core/proactive/` | — | **Orphaned**: 899 LOC, 0 importers (F4) |
| `continuous_intelligence` | `aura_core/continuous_intelligence/` | — | Empty package (17 LOC) (F4) |
| `ClientAgentOrchestrator` | `aura_client/lib/core/ai/orchestrator/` | — | Retired stub that throws 410; kept for cutover tests |

Selection and enforcement work as designed. The client lists personas from `GET /api/v3/agents`,
locks any persona whose `usable` is false, and sends `"agent"` on `/chat/stream`. The kernel
resolves through `AgentRegistry.resolve` and falls back to `orchestrator` when the id is unknown
or the caller lacks a required capability. The message bubble shows when the resolved persona
differs from the one requested. The grant travels in `ToolContext` into `ToolPolicy.evaluate`.

## Findings

### Critical

**F1. Production is down, so no agent is reachable.** On 2026-11-16, `aureon-ai.tech`
`/health/live`, `/health/ready` and `/api/v3/agents` all return `404 Application not found` with
`server: railway-hikari` and `x-railway-fallback: true`. That means Railway's edge has no service
bound to the domain. `docs/status/` still describes production as up; its last live check was
2026-11-07. Only the owner can check the Railway service and custom-domain binding.

### High

**F2. No agent is proven live.** The Engineer has no `verify-engineer-workspaces` report
(GAP-025) and no `aura_hostd` end-to-end report (GAP-026). Host Daemon CI has not executed since
`dc87beb`. The agent tools for tasks, finance and health write tables that are not deployed (CB-28).
Backend CI has not executed on `main` since `dc87beb` (CB-44). Everything above is local evidence.

**F3. On Railway, the Engineer has only two working paths.** The Docker sandbox is off there by
design (`docs/deployment/control-plane.md`), so `write_file`/`run_command`/`git_*` work only for a
user whose `aura_hostd` is paired. Otherwise they fail at call time, as `_build_sandbox_runner`
intends. The Dockerfile defaults to `AURA_WORKERS=4`. A paired desktop is reachable from every
worker only when the host-agent relay is on, which needs `AURA_REDIS_URL` and a JWT secret.
Without them, about three Engineer turns in four find no desktop, and the only sign is a warning
at boot. The control-plane guide does set Redis, but whether the live service actually has it is
unverified. The other path is the `github_*` tools, which need GitHub configured.

**F4. About 1.8k lines of orphaned agent code.**
- `engineering2/` calls itself "Engineer/Coding Agent 2.0". It claims sandbox policy, no force
  pushes, credential isolation and approval gates, yet nothing imports it and no test covers it.
  A reader can easily believe those controls protect the live Engineer. They don't: the live
  controls are the ones in `tools/coding.py`, `ToolPolicy` and `docker_sandbox.py`.
- `proactive/` has no importer. The `proactive` strings in `llm/workload.py`, `ports/storage.py`
  and `triggers/engine.py` are words, not imports.
- `continuous_intelligence/` is empty.

GAP-008 (open, P2) already covers `engineering2`, `proactive` and `continuous_intelligence`. The
exit condition is still "tested and wired, or deleted".

**F5. The eval gates exercise only the orchestrator and a synthetic persona.**
`agent_tasks.jsonl` has 50 cases and none sets an agent, so all of them run as `orchestrator`.
`agent_runs.jsonl` has 12 cases, all against a synthetic `EVAL_AGENT_ID` persona with `eval_*`
tools. None of the seven specialist personas' prompts or grants is evaluated end to end. Unit tests
pin the grants, but not behaviour. The eval gate is also not a required check, and `main` is
unprotected (eval-ci.yml's own header).

### Medium

**F6. Generated (`bp_*`) agents can't be delegated to.** `build_delegation_tools` computes the
`target_agent_id` enum once, at container build. A blueprint deployed later swaps the
`ActiveBlueprint` snapshot, but the enum stays at the seven core personas. A probe confirmed this:
after `swap([bp_orders])` the registry contains `bp_orders` and the enum does not.
ADR-021 says "`delegate_to_agent` sub-turns carry the same person's tokens". For generated
connector agents, that path is unreachable. Either rebuild the enum on swap or have the handler
check `agents` at call time, or record in ADR-021/024 that generated agents are not delegation
targets.

**F7. The desktop Engineer panel changes the app-wide persona and never restores it.**
`AgentPanel.initState` calls `activeAgentIdProvider.setAgent('engineer')`, and `dispose` does not
set it back. After someone opens the panel, ordinary chat keeps sending `agent: engineer`. The panel
also ignores `usable`. On an account without `engineer_agent`, the persona picker locks Engineer,
but the panel still selects it, and the backend quietly answers as `orchestrator`.
Client modules `agent`, `host` and `terminal` have no tests (DESKTOP_APP_STATUS).

**F8. Delegating write work always fails.** `delegate_to_agent` refuses any sub-turn that needs
confirmation. Task, finance and health writes and every Engineer write need confirmation, so
delegation is only useful for read-only sub-tasks. The tool's description says so, but the
orchestrator prompt's description reads "general assistance and delegation". This is deliberate,
not a bug. It is a limit worth stating wherever delegation is described to users.

**F9. The gap register is out of date for agents.**
- GAP-024 `current_state` says "every persona uses memory_scope=shared". Today `finance_advisor`,
  `health_companion` and `engineer` have their own scopes, and the kernel reads own + shared
  (`kernel.py:1094`).
- GAP-010 lists `aura_hostd/build/*` binaries and logs that are no longer tracked. Two stray tests
  remain: `aura_backend/test_personal_os2.py` and `aura_backend/test_personal_os_sync_integration.py`.

### Low

**F10. `coding_agent` is a prompt, not an agent.** It says "give working code" but its tools are
the read tools plus a calculator. The client already notes that it is "much weaker" than
`engineer`. Users can pick it expecting the Engineer's capabilities. Merge it, rename it, or say so
in its description.

**F11. `decision_agent` is never told its tools.** Its template has no `capabilities` block,
unlike every other persona's, so the model never sees its four read tools listed.

**F12. Whether `research_agent` can reach the web on Railway is unknown.** It has `fetch_url` only
when `AURA_HTTP_ALLOWED_HOSTS` is set. Otherwise it researches only the user's own knowledge base.

**F13. Client dead code.**
- `AgentConfigRepository` and the Drift `agent_configurations` table save per-persona custom
  instructions that are never sent anywhere. The repository has no callers.
- `agents_repository.dart`'s header says personas come from `prompts/library.py`'s
  `AGENT_PROMPT_IDS`. They now come from `agents/registry.py`.

## Fixed in this change

| Finding | Fix | Test |
|---|---|---|
| F4 | Deleted `engineering2/`, `proactive/` and `continuous_intelligence/` (1,835 LOC) after re-checking that nothing imports them. GAP-008 stays open for the `security/` modules | full `pytest` and legacy `unittest` |
| F6 | `delegate_to_agent`'s spec reads its `target_agent_id` enum from the live registry, and the handler refuses an unknown id by name before `resolve()` can fall back to the orchestrator | `tests/unit/test_delegation.py::TestTargetsFollowTheLiveRegistry` |
| F7 | The desktop Engineer panel switches to Engineer only when `/api/v3/agents` shows it is usable (or cannot be fetched, as before). It shows a locked state otherwise, and restores the previous persona on close unless the user changed it | `aura_client/test/desktop/agent_panel_persona_test.dart` |
| F9 | Dated notes on GAP-008, GAP-010 and GAP-024; `GAP_REGISTER.md` re-rendered | `tests/architecture/test_gap_register_schema.py` |
| F11 | `system.decision_agent` 1.2.0 declares and renders `capabilities` | `tests/unit/test_agents.py::TestEveryPersonaIsToldItsTools` |
| F13 (part) | `agents_repository.dart`'s header names the right source of personas | none needed |

Verification, run locally with no CI behind it:

- Backend `pytest`: 4833 passed, 247 skipped.
- Legacy `unittest`: 146 run, OK (37 skipped).
- `flutter analyze`: clean.
- `flutter test`: 692 passed (4 skipped).

`docs/guides/api-reachability.md` was regenerated, because only the client line numbers moved.

Not done here, because each needs the owner or a decision: F1, F2, F3, F12 (live checks and
Railway settings), F5 (per-persona eval cases and branch protection), F8 (a product decision),
F10 (merge or rename `coding_agent`), and the rest of F13. Dropping the `agent_configurations`
Drift table needs a schema migration.

## Suggested order

1. Owner: restore the Railway service and domain binding (F1), then re-check `/health/ready` and
   `GET /api/v3/agents`.
2. Owner: confirm on Railway that `AURA_REDIS_URL` is set and the relay is on (no "host-agent
   relay off" warning at boot) (F3). Also check whether `AURA_HTTP_ALLOWED_HOSTS` is set (F12).
3. Delete or wire `engineering2/`, `proactive/` and `continuous_intelligence/` (F4, GAP-008).
4. Fix the desktop panel's persona handling (F7) and the delegation target list (F6).
5. Add per-persona cases to `agent_tasks.jsonl` (an `agent` field and a check on the resolved
   agent), and make the eval gate a required check once branch protection exists (F5).
6. Produce the GAP-025 and GAP-026 reports. That is the only route to "proven" for the Engineer
   (F2).
7. Correct GAP-024 and GAP-010 (F9). Then F10, F11, F13.
