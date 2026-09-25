# ADR-016 — Agent Platform 2.0: one bounded loop, durable runs, scoped memory

**Status:** Accepted (built, not proven on production traffic) · **Date:** 2026-09-25

## Context

Eight personas are registered in `aura_core/agents/registry.py`, but until this change only
`engineer` ran a multi-iteration plan → execute → replan loop, nothing recorded what a turn did as
a whole, and `AgentSpec.memory_scope` existed while every persona read and wrote one shared memory
(GAP-024). The roadmap's Phase 7 asked for durable run state, a standard bounded loop, scoped agent
memory and run-level evaluation — without duplicating kernel logic or giving agents a second path
to tools.

## Decision

1. **One loop for every persona.** `aura_core/execution/agentic_loop.py` is the outer loop around
   the existing `PlanningEngine` and `ExecutionEngine`: understand → plan → execute → observe →
   evaluate → replan. A persona with `max_plan_iterations > 1` (today `engineer`) may replan;
   every other persona runs the same loop with a replan limit of zero, which is exactly the single
   `ExecutionEngine.execute` pass it had before. Tool calls still reach the registry only through
   planning/execution and `ToolPolicy` (ADR-006); the loop changes how many times the pair runs,
   never how a call is dispatched.
2. **Five budgets, as data.** `AgentSpec` carries `max_iterations`, `token_budget` (planner
   generation tokens), `time_budget_s` (on the injected clock, never looser than the caller's
   deadline), `tool_budget` and `risk_budget`. The last two are enforced *before* dispatch by
   `RunBudgetGate`, so a batch cannot overshoot them; the gate can only refuse.
3. **Durable runs.** `domain/agent_runs.py` defines `AgentRun` (one per planned turn, any persona)
   and `AgentStep` (observations, decisions, tool calls, approvals, evaluations, failure,
   completion). They extend the engineer-runs port and adapter rather than adding a parallel store,
   and live in `aura_agent_runs` / `aura_agent_steps` (Alembic `0008`). **Content rule:** no raw
   user message or model text is stored — the goal is a digest and a length, tool arguments are a
   digest, and `safe_detail` drops anything that is not a short scalar.
4. **Scoped memory.** Memory is isolated per (user, `memory_scope`); `SHARED_MEMORY_SCOPE`
   (`"shared"`) is the documented default most personas use, while the finance and health advisors
   get their own scopes. Cross-agent and cross-user isolation are tested.
5. **Observable and evaluated.** Owner-scoped `GET /api/v3/agents/runs` and
   `/api/v3/agents/runs/{run_id}` (covered by the route-auth and IDOR suites), run metrics for
   iterations and budget exhaustion, and a deterministic `agent_runs` evaluation suite gated in
   `evaluation/gate.py` alongside the existing suites.

## Consequences

- Engineer and single-pass personas share one code path, so a budget or policy fix lands for all of
  them at once.
- The run ledger answers "what did this turn do" without storing what the user said.
- **Not yet proven:** no production traffic has exercised the loop or the run tables; the
  evaluation suite uses scripted providers; no client or admin view reads the run endpoints yet
  (see `docs/guides/api-reachability.md`).
