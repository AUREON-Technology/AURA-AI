# ADR-037 — The agent loop: explicit states, recovery by failure kind, a stuck detector, a critic, and a token monitor

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust
channels, confirmation), ADR-011 (desktop engineer agent), ADR-013/014 (engineer workspaces),
ADR-036 (open analysis tools) · **Gap:** the "AI Agent Tools Inventory" rows 14, 28, 42, 43, 44

"Built, not proven" is meant literally. Every behaviour below exists, is wired into the real
`AgenticExecutor`, and is exercised by tests that drive a real Engineer turn through `AIKernel` (real
planner, registry, policy, execution engine and loop; a real `pytest` run in a real directory). What
has **not** happened is a run against a production model on a production deployment: the model in
every test is a scripted stand-in, so none of this says how often a real planner makes the mistakes
the critic looks for, or whether the thresholds are tuned. That needs the evidence the roadmap's
exit criteria name.

## Context

`AgenticExecutor` (`execution/agentic_loop.py`) already bounded the plan → execute → observe →
replan loop by iterations, tokens, wall time, tool calls and risk. Five things were still missing,
each visible in a real failure:

1. **Nothing noticed a loop going in circles.** Only the hard bounds stopped it, so one identical
   failing plan ran nine times with eight replans.
2. **Every failed read-only step was retried identically.** A step whose arguments failed
   validation fails the same way twice; a policy denial is a decision, not a fault.
3. **The loop could not see a red test suite.** `run_command` returns `ok=True` whenever the
   command *ran*; the exit status and the runner's summary live only in the result text. A plan
   whose every step "succeeded" ended `completed` with the tests failing.
4. **No second look.** The planner could name a tool that does not exist, edit a file it never read,
   or change code and never run the tests; the answer could claim success over a failed step.
5. **The Engineer had no token budget and nothing reported use.**

## Decision

All of it is **additive and optional**: `AgenticExecutor` takes each piece as a constructor argument
that defaults to off, the container wires them from settings, and the loop only applies them to a
persona that replans (`max_plan_iterations > 1`; today `engineer`). A single-pass persona runs
exactly the loop it always did. No tool is dispatched by any new code: the loop still only calls the
planner and `ExecutionEngine`, so ADR-006's rules (registry-mediated dispatch, taint, confirmation)
are untouched.

### 1. The loop as a graph (`execution/state_graph.py`)

```
PLAN ──planned──▶ EXECUTE ──executed──▶ OBSERVE ──┬─success──▶ VERIFY ──verified────▶ DONE
  │                  ▲                              ├─replan───▶ REPLAN ─replanned─▶ EXECUTE
  └─critic_revise─▶ REPLAN ◀──────corrective────────┤                    └replan_failed▶ ESCALATE
                                                    ├─stuck────▶ STUCK
                                                    └─escalate─▶ ESCALATE
```

`TRANSITIONS` is the whole graph as a table; `transition()` is the only way to move and raises on an
edge that does not exist. `classify(Signals)` is the OBSERVE decision, the old `_evaluate` ladder in
the same order with the new inputs slotted in:

1. every step succeeded **and no unresolved test failure** → `success` (`completed`)
2. past the deadline → `time_budget_exhausted`
3. the pass's own terminal block (`needs_confirmation`, a budget) → that reason
4. recovery says **stop** → `unrecoverable:<kind>`; every failure is a refusal → `not_retried:<kind>`
5. nothing repairable → `nothing_to_repair`; single-pass persona → `single_pass`
6. **stuck** → `stuck`
7. an unrecognised failure that came back after a replan → `escalated:<kind>`
8. generation tokens spent → `token_budget_exhausted`; no replans left → `iteration_budget_exhausted`
9. otherwise → `replan`

`DONE`, `STUCK` and `ESCALATE` are terminal; `ESCALATE` means "stop and tell the user". Every stop
reason the run recorder, metrics and tests already knew is unchanged; new ones are `stuck`,
`unrecoverable:*`, `not_retried:*`, `escalated:*`. `AgentRunStatus.STUCK` is a new terminal run
status (a text column; no migration). The states a run visited are reported as `agent_loop.trace`.

**Test results are an input** (`execution/runner_output.py`). The parser reads what the tool already
returns, without changing any tool's result shape: the `exit_code=` header and the runner's own
summary for pytest, unittest, jest, vitest and `go test`, and the JSON `generate_and_run_tests`
returns. It is pure, bounded and tolerant (garbage → no signal, never an exception). Each
succeeded `run_command` / `generate_and_run_tests` step becomes an observation:

| observation | when |
|---|---|
| `tests_failed` | a runner summary shows failures/errors (even if the exit was masked, e.g. `pytest \|\| true`), or a test-like command exited non-zero |
| `tests_passed` | a green summary, or a test-like command exited 0 |
| `command_failed` | any other command exited non-zero with some output |
| `command_ok` | exit 0, or exit 1 with **no output** (`grep` with no match, `diff` with no difference) |

A failure is **unresolved** if it is the last observation of its family (test runs; other commands)
— a red run followed by a green one is resolved. With `AURA_LOOP_TEST_SIGNALS_ENABLED` on, an
unresolved failure makes the pass "not done" and replans with the reason
`tests_failed (pytest: 2 failed, 5 passed; failing: test_a, test_b)`;
`PlanningEngine.replan(feedback=...)` puts the observation and an instruction to plan the fix *and a
new step that re-runs the tests* into the revision prompt (a completed step's result is otherwise
preserved and not repeated).

### 2. Recovery by failure kind (`execution/recovery.py`)

A pure `RecoveryPolicy`, used by the engine (per failed step) and the loop (per failed pass):

| class | `error_kind` examples | decision |
|---|---|---|
| transient | `timeout`, `tool_timeout`, `rate_limited`, `dependency_unavailable`, `ConnectionError`; or a generic `tool_execution_error` whose message says timed out / 429 / 503 / unavailable | **retry the same step with exponential backoff** (read-only only; `0.5s`, `1s`, … capped at `8s`), up to the step's `max_attempts`; then replan |
| validation | `validation_error`, `unresolved_argument`, `unresolved_reference`, `tool_not_found`, `invalid_output` | **never retry the same call**; replan carrying the validator's message |
| denied | `policy_denied`, `confirmation_required`, `consent_required` | **do not retry, do not work around**; surface to the user. A pass that is *all* refusals ends the run (`not_retried:*`); a mixed pass replans with the refusal named |
| security | `output_withheld`, `safety_violation` | **stop**: no retry, no replan (`unrecoverable:*`) |
| unknown | anything else | no blind retry; **replan once**, and if the same kind returns, **escalate** (`escalated:*`) |

A side-effecting step is never retried in place whatever its class (it may have acted before it
failed): transient becomes "replan". The engine's `max_attempts` stays the **ceiling** — the policy
can only refuse a retry or delay it. `ExecutionOutcome.failure_messages` (new; each failed step's
tool message, 400 characters) is how the validator's text reaches the replan. Behind
`AURA_LOOP_RECOVERY_POLICY_ENABLED` (default **off**: the original "retry any failed read-only
step" rule).

### 3. Stuck detection (`execution/stuck.py`)

A `StuckDetector` watches each pass and stops a loop that is *already about to replan*; it never
acts on a pass that succeeded. Three rules, each configurable, `0` disables one:

| rule | default | trips when |
|---|---|---|
| `identical_calls` | 3 | the same `(tool, arguments)` produced the same outcome (same failure, or same output) N times — a rerun whose *output changed* is movement and resets the count |
| `same_error` | 3 | the same `error_kind` (or failing-test signature) in N consecutive passes |
| `no_progress` | 4 | N consecutive passes in which no step newly succeeded |

The stop is a `stuck` outcome: a `stuck` stream frame (`rule`, `detail`, `count`, `tool`,
`escalation`), `agent_loop.stuck` / `agent_loop.escalation` on the `done` frame and `/chat`
response, run status `stuck`, and the **escalation message appended to the answer**: what was
repeated, and what to try ("give the tool different arguments, use a different tool, or fix what the
failing output points at"). A one-line system note tells the model not to claim success. On by
default (`AURA_LOOP_STUCK_DETECTION_ENABLED`) because it only ever stops a spinning loop.

### 4. The critic (`execution/critic.py`)

Deterministic rules first; each finding is `{code, severity, message, step_id, blocking, source}`.

**Plan, before execution:** `unknown_tool` (a step names a tool that is not available — error,
blocking); `write_without_read` (a side-effecting step with nothing earlier that reads; creation,
`run_command` and verification steps are exempt; blocking only for `edit_file`); `no_verification`
(the plan changes code files and no later step runs tests or otherwise checks — blocking). **A
blocking finding buys one revision before anything runs**, with the findings fed into
`PlanningEngine.replan(feedback=...)`; at most one per run, only if the iteration and token budgets
allow.

**Outcome, before the answer (VERIFY):** `unverified_change` (code was changed and no later step
succeeded in verifying it — blocking: **one corrective pass**, then the run ends either way);
`unresolved_tests_failed` (error; a caveat); `failed_steps_unresolved` (warn).

**Answer, after generation:** `success_claim_unsupported` (the answer reads as success while a step
failed or the last test run is red) and `unsupported_reference` (a file or test named in the answer
that no tool result, argument or the request mentions). These become caveats **appended** to the
answer (as further `token` frames on the stream), never a silent rewrite.

An optional `LLMCritic` (`AURA_CRITIC_LLM_ENABLED`, default **off**) adds advisory findings from the
model for the plan and the outcome; its findings are always `source="llm"` and never blocking, and
any failure yields none. The deterministic critic is on by default (`AURA_CRITIC_ENABLED`) **for the
Engineer loop only**.

### 5. Token budget and context pressure (`execution/token_monitor.py`)

The Engineer gets `token_budget` from `AURA_ENGINEER_TOKEN_BUDGET` (default `400000`; `0` =
unbounded; the registry's own default is unchanged for callers that pass nothing). Every pass
emits a `budget` frame `{budget, used, remaining, percent, generation_tokens, held_output_tokens,
iteration}`, where `used` = planner generation tokens + an estimate (characters / 4) of the tool
output the plan holds. The hard stop is unchanged: only generation tokens exhaust the budget.

When `used` first reaches `AURA_CONTEXT_PRESSURE_THRESHOLD` (default `0.8`) of the budget, once per
run: a `context_pressure` frame with recommendations (`compress_tool_output`, `summarise_history`,
`trim_context`); the tool output that **earlier passes** left in the plan is shortened to head and
tail (the newest pass is untouched); and after the loop the kernel calls
`EpisodicMemory.compact` — the existing session summariser, on demand — to fold the session's older
turns into its summary for the next turn.

What this does **not** do, stated so nobody assumes it: the loop's own prompts carry step
descriptions, never tool output, and the final answer is built from the last pass's results
(`ExecutionOutcome.results` holds one pass), so the shortening affects what a long run *retains*
(the plan on the outcome, the critic's evidence), not a prompt already on its way to the model.
Retrieval trimming is a recommendation only.

## Configuration

| variable | default | |
|---|---|---|
| `AURA_LOOP_STUCK_DETECTION_ENABLED` | `true` | the detector |
| `AURA_LOOP_STUCK_IDENTICAL_CALLS` / `_SAME_ERROR` / `_NO_PROGRESS` | `3` / `3` / `4` | thresholds; `0` disables a rule |
| `AURA_LOOP_RECOVERY_POLICY_ENABLED` | `false` | failure-kind-aware retries |
| `AURA_LOOP_RECOVERY_BACKOFF_S` | `0.5` | first transient backoff (doubles, cap 8 s) |
| `AURA_LOOP_TEST_SIGNALS_ENABLED` | `false` | failing tests make a pass "not done" |
| `AURA_CRITIC_ENABLED` | `true` | deterministic critic (Engineer loop only) |
| `AURA_CRITIC_LLM_ENABLED` | `false` | advisory model critic |
| `AURA_ENGINEER_TOKEN_BUDGET` | `400000` | `0` = unbounded |
| `AURA_CONTEXT_PRESSURE_THRESHOLD` | `0.8` | fraction of the budget |

The defaults that change an existing turn's behaviour are the stuck detector (it only stops a loop
that is already spinning) and the Engineer's critic and token budget (the row asks for them);
failure-kind retries and test-result replans are **off** until an operator turns them on, because
both change what a turn does. Compose files, `.env.example` and the README list all nine.

## Stream and response surface

New frame types on `/api/v3/chat/stream` (a client that does not know them ignores them — the Flutter
client's `SseChatEvent.fromWire` maps unknown types to `unknown`): `stuck`, `budget`,
`context_pressure`, `critic` (`stage`: `plan` | `outcome` | `answer`). The `done` frame and the
`/chat` response carry `agent_loop` (`stop_reason`, `trace`, `stuck`, `escalation`, `findings`,
`observations`, `budget`, `pressure`, `history_compacted`) when a loop ran. The Flutter client does
not render any of these yet; that is client work.

## Consequences

* One planner generation can be spent on a critic revision before a run starts, and one on a
  corrective pass after; both count against `max_plan_iterations`.
* `unsupported_reference` is a heuristic over file-like and `test_*` names; it can flag an answer
  that mentions a real file the run never touched. It is phrased as "unverified", an `info` finding.
* The stream path does not feed the model the failed-step execution note that `AIKernel.run` does
  (found while wiring the loop's own notes; left as it was, it is a separate defect).
* Not done here: per-tool-family recovery overrides; learning thresholds from run history; a
  summariser that compresses tool output instead of clipping it.

## What would prove it

Run the Engineer against a real model on a task with a failing test suite and check, from the run
records: (a) the loop stops on `stuck` within three passes on a deliberately unwinnable task, with
the escalation in the answer; (b) a red suite becomes a replan and a green one ends the run
(`AURA_LOOP_TEST_SIGNALS_ENABLED=true`); (c) the critic's plan findings fire on real planner output
at a rate worth the planning call each costs; (d) the 400k budget is not hit by ordinary tasks. Until
a run like that is on file, this is built, not proven.
