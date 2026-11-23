# ADR-045 — Agent behaviour for partial rows: acceptance criteria on a turn, feedback on a held plan, model-written lessons, recovery proven end to end, deterministic output digests

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust channels,
confirmation), ADR-008 (the system proposes, a person promotes), ADR-030 (data classification of
generations), ADR-037 (agent loop: recovery, stuck detection, token budget), ADR-038 (plan modes, auto
lessons), ADR-041 (agent task gateway) · **Inventory rows:** 41, 51, 60, 44, 28

## Context

Five rows were registered and working as tools or as engine pieces, but each stopped short of the
agent's own behaviour (`still_missing`):

* **41** `verify_acceptance_criteria` existed, but nothing set `Goal.success_criteria` and nothing checked a
  finished run against it: a run could end "successfully" with every criterion unproven.
* **51** `apply_plan_feedback` edited a list of strings, but no plan held for approval could be given
  feedback; the client could only approve or walk away.
* **60** Lessons were extracted by keyword and plan shape only.
* **44** The failure-recovery policy (`AURA_LOOP_RECOVERY_POLICY_ENABLED`) was proven on the engine and on
  a scripted loop, never through a kernel turn.
* **28** Tool output retained under context pressure was cut to its head and tail, which drops the one
  line a later pass needs whenever it sits in the middle.

The restriction on touching the agent was lifted for this work. Everything below is additive and off
unless the request or the operator opts in; a request that carries none of the new fields behaves, and
is shaped, exactly as before.

## Decision

### 1. Acceptance criteria on the turn (row 41)

`TurnRequest.success_criteria` (tuple, at most 20 of 300 characters, `execution/acceptance.py::
normalise_criteria`) is accepted on `POST /api/v3/chat`, `/chat/stream` and `/agents/tasks` (and carried in
the task spec). The kernel passes it to `goal_from_message(message, criteria=...)`; the planner prompt lists
them (so the plan includes steps whose output shows each one). After the plan or agent loop ends,
`check_acceptance` reads **the run's own evidence** -- succeeded tool steps' output, the last test run
(`observe_plan`), failed steps that name a criterion -- and sorts each criterion:

| bucket | evidence |
|---|---|
| `met` | a tool-output line that names the criterion (word boundaries) and carries a pass marker and no failure marker, or a green last test run for a criterion about tests |
| `unmet` | the same with a failure marker, a red last test run, or a failed step whose description names it |
| `unverified` | nothing the run produced speaks to it |

The model's own answer is deliberately not evidence. The result is `agent_loop.acceptance = {status, met,
unmet, unverified, basis}` (`status`: `met` | `unmet` | `unverified`), in the stream `done` frame, in the
`/chat` response (only when criteria were sent) and in the task's `result.agent_loop`. A run that did not
meet every criterion **appends a caveat to its answer** ("Acceptance check ... Not met: ..."), whether or
not a loop ran; a plan held for review is not judged before it runs.

### 2. Feedback on a held plan (row 51)

`POST /api/v3/chat/plan-feedback {approval_ids, feedback, session_id}` (`planning/feedback.py`,
`AIKernel.revise_held_plan`). Nothing runs. The comment is classified and, when it names steps, applied with
the same step editor the `apply_plan_feedback` tool uses (`open_pure.apply_step_feedback`); an approval with
no change or a question leaves the plan and its ids alone (`revised: false`). Anything else is **one**
`PlanningEngine.replan` (the planner is told the current plan, the person's words and the edited outline;
succeeded steps are kept; `replan` refuses past `AURA_PLANNING_MAX_REPLANS`, so feedback is bounded however
often it is sent). The revised plan is held under **new** approval ids, then the old hold is consumed
(`PendingConfirmationStore.peek` reads without consuming; `claim` consumes): an approval given for the plan
the person is no longer looking at cannot run it, a failed replan (or the planner's own "respond directly"
fallback) leaves the old hold approvable, and two concurrent comments revise once. Owner- and session-scoped
by the store (a stranger gets the same 409 as for a bad approval); audited as `plan.feedback` with intent
and revision count, never the plan text or the comment. Service keys are refused.

### 3. Model-written lessons (row 60)

`AURA_AUTO_LESSONS_MODEL=true` (default false; needs `AURA_AUTO_LESSONS_ENABLED`). After an Engineer turn
`learning/model_lessons.py` asks the router for lessons from the run's summary: the user's message, the
answer and the plan's shape (never tool output or arguments; tainted turns are skipped). The request is
`data_classification=SENSITIVE` (an Engineer turn's baseline, `llm/sensitivity.py`), so the router excludes
any provider whose ceiling is below it -- a hosted open-weight provider's is `INTERNAL`; with none cleared,
the call fails and the heuristic answers. The reply is validated strictly (`{"lessons": [{"kind",
"text"}]}`, at most five, known kinds, 20-300 characters, no control characters or unknown keys; anything
else discards the whole reply). Candidates then take the **existing** road in `learning/jobs.py` (secret
drop, injection screen, duplicate check) into the owner's pending queue; nothing is active until approved.
Any failure, an empty list or a non-conforming reply falls back to the keyword extractor, and it never
blocks the turn (it runs in the background, off the answer's path). `extractor` in the job report says
which path produced the lessons.

### 4. Recovery proven through the kernel (row 44)

The default stays **off**. `tests/unit/test_recovery_e2e.py` runs real Engineer turns (real registry, policy,
execution engine, agent loop and `PlanningEngine.replan`; only the model's plan is scripted) with the flag
on: a transient read failure is retried in place and the run completes; a transient side-effecting failure
is replanned, not repeated in place; an invalid call is never dispatched and the validator's message reaches
the replan; a policy refusal stops the run with `not_retried:policy_denied` and the answer says so; an
unrecognised permanent failure is replanned once and then escalated (`escalated:*`) in two dispatches where
the flag-off run spends its whole budget retrying. The same on the streaming path. The test found no defect.

### 5. Deterministic digest of clipped output (row 28)

`execution/token_monitor.digest_omitted`: when output is clipped under context pressure, the distinct
failure/warning lines of the omitted middle (first occurrences, repeat counts, at most 8 lines and 500
characters, each cut to 110) are kept in one line after the omission marker. No model. Output with nothing
salient is clipped exactly as before; clipping is idempotent (a clipped output is recognised, not clipped
again).

## What this does not do (stated so the rows are not over-read)

* **Row 41** matches by phrase on word boundaries: evidence worded differently from the criterion stays
  `unverified`, never guessed. It does not judge behaviour; it reads what tools printed. The planner is
  *asked* to produce evidence, a model that does not will leave criteria `unverified`.
* **Row 51** does not edit `Plan` objects mechanically: the planner writes the revised plan, so a revision
  is only as good as that model. The revision allowance is shared with the agent loop's own replans
  (`plan.revisions`). The client has no feedback UI yet (`scripts/api_reachability.py` triages the route
  as "to wire").
* **Row 60** is unproven against a real local model: the tests use the echo provider with scripted JSON. A
  3B model may often fail the strict contract, in which case the heuristic lessons (the previous behaviour)
  are what the owner sees. The call adds load on the local provider after each Engineer turn.
* **Row 44** is still off by default, and unknown-class failures lose the old single blind retry when it is
  enabled.
* **Row 28** stays PARTIAL: there is still no model summariser of retained output (deliberately: it would be
  another generation on the CPU path), retrieval trimming is a recommendation only, token use is a
  characters/4 estimate, and the Flutter client does not render `budget` or `context_pressure` frames.

## Consequences

Four request/response additions (`success_criteria`, `agent_loop` on `/chat` when criteria were sent, the
plan-feedback route, a task-spec field), one setting (`AURA_AUTO_LESSONS_MODEL`), one `KernelDependencies`
field (`audit`), and `PendingConfirmationStore.peek`. No migration, no new dependency, no new table.
