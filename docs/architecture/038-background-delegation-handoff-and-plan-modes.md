# ADR-038 — Background delegation, structured handoffs, session checkpoints, automatic lessons and plan modes

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust
channels, taint, confirmation), ADR-008 (human-in-the-loop promotion), ADR-016 (agent platform),
ADR-036 (open analysis tools) · **Inventory rows:** 26, 57, 59, 60, 63

## Context

The AI Agent Tools Inventory listed five capabilities as PARTIAL: the code existed, nothing in the
product reached it, or it did less than its name.

| Row | What existed | What was missing |
| --- | --- | --- |
| 26 `session_checkpoint_summarizer` | `memory/checkpoint.py` and `EpisodicMemory.checkpoint()` | no caller: no tool, no route, no trigger |
| 57 `agent_manager_orchestrator` | `delegate_to_agent`, a synchronous one-hop sub-turn | the orchestrator blocks for the whole sub-turn; no way to run, follow or stop work in the background |
| 59 `agent_handoff_state_store` | the same tool | a handoff carried one string; the child started blind, and the result came back as prose |
| 60 `knowledge_base_curator` | nightly knowledge refresh (Loop A) | nothing turned what an Engineer run learned into memory |
| 63 `planning_mode_switcher` | `plan: bool` on `/chat` (no client sends it) and a heuristic; the Engineer always plans | no named modes, no read-only fast path, no way to see or approve a plan before it runs |

The project owner lifted the earlier "do not touch the agent" restriction for this work and set the
scope to backend and API. Each change below is small, additive, covered by tests that go through the
real wiring, and **off or unchanged by default**.

## Decision

### Row 26 — session checkpoints are reachable

* Tools `save_session_checkpoint` and `recall_session_checkpoint` (`tools/session_tools.py`),
  registered whenever episodic memory exists, granted to the Engineer (the orchestrator holds every
  tool). `ToolContext` gained a `session_id` slot, set by the kernel; the tools need it and
  `metadata["session_id"]` was a string-keyed side channel. Saving is `side_effecting` at PERSONAL
  sensitivity, like `remember_fact`: in a quarantined turn it waits for confirmation.
* `GET` / `POST /api/v3/sessions/{session_id}/checkpoint`. The checkpoint key is namespaced by the
  caller's own id (it always was), so naming another user's session id reads an empty slot of your
  own namespace: 404, indistinguishable from "never checkpointed". `POST` counts against the chat
  budget because it may be a model call. Service keys have no sessions and are refused.
* `AURA_MEMORY_AUTO_CHECKPOINT_EVERY_TURNS` (default `0`): through `EpisodicMemory.append`, the one
  path every persisted exchange takes, every Nth turn schedules a checkpoint **in the background**.
  The model call never sits on the turn's path; a failure is logged and the next Nth turn retries.

### Row 57 — background delegation

`delegate_to_agent(..., background=true)` returns `{"agent_task_id": ...}` at once. The sub-turn runs
as an asyncio task owned by `AgentTaskManager` (`agents/tasks.py`).

* **Same rules, because it is the same request.** `delegate_to_agent` builds one child `TurnRequest`
  (depth ceiling, target and capability checks, grant intersection, risk ceiling, inherited taint,
  origin, connector tokens) and either awaits it or submits it. All checks run at submit time, before
  anything is queued; a test compares the two child requests field by field. A child that needs
  confirmation is a `failed` task with the same message the synchronous path raises: delegation still
  does not support nested confirmation.
* **State is shared, the task is not.** A task's record (status, result, error, timestamps, handoff
  id) lives in the cache port, so any worker answers `get_agent_task` / `list_agent_tasks`. The
  running task is an `asyncio` task in **the process that started it** and does not survive a restart.
  A record still `running` well past its deadline and not live in the reading process is reported
  `failed: lost` rather than left running forever.
* **Bounds.** `AURA_REASONING_AGENT_TASKS_PER_USER` (default 3, counted from the shared records, so
  it holds across workers up to a small race), `AURA_REASONING_AGENT_TASKS_GLOBAL` (default 16, per
  process), `AURA_REASONING_AGENT_TASK_DEADLINE_S` (900), `AURA_REASONING_AGENT_TASK_TTL_S` (3600:
  how long a record stays readable; every write refreshes it).
* **Owner-scoped.** Keys carry the owner's id; another user's task id is "not found" for get, cancel
  and list alike.
* **Cancellation.** Local: `task.cancel()`. From another worker: a `cancel_requested` flag on the
  shared record, which the owning worker's watcher acts on within a poll interval. A late result never
  overwrites a cancel. `Container.shutdown()` cancels what the process runs and writes the final
  records first.
* **The task runs in a fresh `contextvars.Context`.** The parent turn's generation deadline, turn
  budget and data-classification scope are per-turn state that must not leak into work that outlives
  that turn; `AIKernel.run` establishes its own.
* **Tools.** `list_agent_tasks`, `get_agent_task` (tagged `untrusted_content`: it returns a child's
  answer, so reading it taints the turn exactly as the synchronous tool does) and `cancel_agent_task`
  (`side_effecting`: confirmed in a quarantined turn). With the setting off none of the three is
  registered and `background` is not even advertised in the tool's schema.

Consequence worth knowing: `delegate_to_agent` is tagged `untrusted_content`, so a turn that starts a
background task is tainted from then on, as one that delegates synchronously always was.

### Row 59 — structured handoffs

`HandoffRecord` (goal, decisions, completed, blockers, pending, relevant file paths, parent session,
checkpoint provenance) is built from the parent session's checkpoint
(`EpisodicMemory.fresh_checkpoint`: a saved one if recent, else a new one, model-written when a model
answers and extractive otherwise; the record says which) and stored owner-scoped in the cache
(`HandoffStore`). `HandoffOutcome` (status, summary, completed, blockers, tools used, error) is stored
back when the child finishes, whichever path ran it, and read with `get_handoff` / `list_handoffs`.

* **DATA channel, never SYSTEM.** The block travels as `TurnRequest.handoff_context`: guarded like a
  retrieved chunk, fenced with the child turn's nonce, dropped if quarantined. It is not `message`
  (USER) and not `caller_context`, which taints a turn by being present. **Taint rules are unchanged:**
  the child already inherits the parent's taint; a handoff the guards quarantine taints it further;
  a clean handoff does not. `handoff_context` is not reachable from the HTTP API.
* **Compact and ordered.** The packer truncates an over-long item from the end, and the default CPU
  deployment caps the whole prompt at 400 tokens (`AURA_LLM_MAX_PROMPT_TOKENS`). The block is
  at most 1,500 characters and ordered goal, blockers, pending, decisions, done, files, so what is cut
  is what the child needs least. On the 400-token default the knowledge section gets a fraction of
  that and the handoff will be cut hard; the feature is meant for a deployment with a hosted slot or a
  raised prompt cap. A test raises the cap and asserts the fenced block is complete.
* A failure to build or store a handoff never fails the delegation. On the background path the id is
  handed out at once and the record -- which may need a model call for the parent's checkpoint -- is
  built inside the task (running the real path showed the tool otherwise blocked on that call).
* `AURA_REASONING_STRUCTURED_HANDOFF` (default off): with it off the child sees only the subtask.

### Row 60 — automatic lessons, pending approval

After an Engineer turn (`run` and `stream`) the kernel hands a `LessonTurn` to an optional sink, in the
background. `learning/jobs.run_engineer_lesson_extraction`:

* derives at most five short lessons (a failure the plan ran into, a correction that fixed one, a
  convention the user stated, a decision) by keyword and plan structure, no model;
* reads only the user's message, the answer, the saved checkpoint and the *shape* of the plan (tool
  names, step descriptions, statuses, error kinds). **Never** tool output, retrieved documents or step
  arguments (the record has no field for them); a turn whose context was tainted is not mined;
* drops a candidate containing a secret (`SecretGuard`) or one the context guards quarantine,
  and one that duplicates an existing memory of the owner's, pending or approved;
* writes `source="auto_lesson"`, `kind=procedural`, `subject="lesson"` into scope
  `engineer:pending`, which no persona's `readable_scopes` includes, with `review="pending"` and a 30-day
  expiry.

**ADR-008's boundary, as code.** The system proposes; a person promotes. `GET /api/v3/memory/lessons`
lists the owner's pending lessons; `POST .../{id}/approve` moves one into the `engineer` scope and
records `approved_by` and `approved_at` on the record (and in the audit log); `POST .../{id}/reject`
deletes it. Only a pending `auto_lesson` of the caller's own qualifies; anything else is a 404.
`MemoryEngine.forget_source("auto_lesson")` removes every one. `AURA_AUTO_LESSONS_ENABLED` (default off).

A lesson carries no `session_id`: recall treats a record with one as that conversation's own, so a
lesson stamped with its source session was invisible to every later conversation (found by running the
real path; the source is kept in `metadata.source_session`). Under the 400-token CPU prompt cap the
Engineer's own system prompt nearly fills the budget and memory gets little or no room, so an approved
lesson only reaches the model on a deployment that raises `AURA_LLM_MAX_PROMPT_TOKENS`.

Not a model's judgement: the extractor is deliberately modest so a reviewer can say yes or no in a
glance. A model-written lesson pass is a follow-up.

### Row 63 — planning modes

`/chat` and `/chat/stream` accept `plan_mode: auto | plan | fast | review`. It wins over `plan`, which
keeps working: `true` means `plan` and `false` means `fast`, each with exactly its old effect. Named
nothing, with `AURA_REASONING_DEFAULT_PLAN_MODE` empty, the decision is today's, bit for bit
(`effective: "default"`).

| Mode | Behaviour |
| --- | --- |
| `plan` | plan, then run (`plan=true`) |
| `fast` | do not plan; a read-only turn is answered directly. A request that *asks for a side effect* while a side-effecting tool is offered **escalates to `plan`** (reported as `escalated_from: "fast"`), so a write is never silently dropped into a chatty "done". The legacy `plan: false` keeps meaning "never plan" |
| `auto` | plan when the persona runs an agent loop (the Engineer), when the existing multi-step heuristics fire, when the message scores as complex (ADR-033's heuristic, its threshold), or when it asks for a side effect a tool could perform; otherwise answer directly |
| `review` | plan, then **stop**: the planned steps are returned for approval and nothing runs |

**The review gate reuses the confirmation mechanism.** The composed plan is stored in
`PendingConfirmationStore` with one approval id per tool step, bound to its tool and arguments, owner,
session and taint. The response carries `plan` (steps `pending`), `pending_tool_calls` (each with
`approval_id` and `review: true`) and `plan_mode.awaiting_approval`. Approving is the existing flow:
send the ids in `approval_ids` and the kernel executes the stored plan; it does not re-plan, ids are
single use, and another user's or another conversation's ids are a 409. Not sending them is declining.
A step whose arguments depend on an earlier step's output (`${s1.output}`) cannot match a digest taken
before that output exists, so it is held again for its own confirmation when reached; the user approves
the plan, and the data-dependent calls individually. A plan with no tool step has nothing to approve
and runs as usual. If the pending turn cannot be stored the turn fails closed (degraded, nothing runs).

**Response metadata.** `plan_mode` on `/chat` and on the stream's `done` frame:
`{requested, effective, reason, planned, awaiting_approval, planning_failed, escalated_from?}`.
`effective` is `default | auto | plan | fast | review | resumed`.

## Alternatives rejected

* **A durable task queue for background delegation** (Celery, a table). A table is a migration (Alembic
  is the only schema authority) and Celery would move the sub-turn out of the process whose
  `ToolContext`, tokens and providers it was built from. Cache records plus an in-process task cover
  "do not block the orchestrator" without either; a restart losing a task is stated, not hidden.
* **Handoff as `caller_context`.** It taints by presence, which would make every handed-off child
  need confirmation for tools it could otherwise run. A separate field with the same guards keeps the
  taint rule where it was.
* **Writing lessons straight into engineer memory.** ADR-008. The pending scope costs one hop for the
  owner and removes the question of what a poisoned turn can plant.
* **`review` as a new execution path.** A second approval mechanism would be a second place for the
  bind-to-the-exact-call rule (F-04) to be wrong. The confirmation store already has it.
* **`fast` = "never plan" for an explicit `plan_mode`.** A write request answered without a tool run
  invites "done" with nothing done. The escalation is visible in the response.

## Consequences

**Positive.** The five capabilities are reachable through the real wiring and tested through it:
a background task started through the tool registry and a real recursive kernel; a handoff fenced as
data in what the model was actually shown; an Engineer turn queuing lessons that no recall returns until
approved; a review turn that provably runs nothing and then runs the stored plan once.

**Negative.** Background tasks do not survive a restart. The per-user cap races by at most one across
workers. The handoff is cut hard on the 400-token CPU default. `auto` is a keyword heuristic and will
sometimes plan a turn that needed none, or not plan one that did; the response says why it decided.
Starting a background task taints the turn, as delegating always did.

**Impact.** Security: no new trust path; every new channel is DATA, owner-scoped and guarded, and every
new write is behind a setting. Performance: unchanged when off. Operability: nine environment
variables, documented in `aura_backend/README.md` and `deploy/.env.example` and wired in both compose
files.

## Built, not proven

Built and tested against the in-memory substrate and the echo provider. **Not proven** until run
against a real deployment with a real model:

* background delegation across two real workers on Redis (cross-worker cancel and loss are tested with
  two managers over one cache, not two processes);
* handoff usefulness on a hosted slot (the tests prove where the block goes and that it is intact, not
  that it makes a child answer better);
* the quality of extracted lessons on real Engineer runs (the extractor is tested on constructed turns);
* `auto` mode's precision on real traffic.

None of the client-side work exists: the Flutter client sends no `plan_mode`, shows no approval card for
a reviewed plan, no lesson inbox and no task list. The new routes are listed as "to wire" in
`docs/guides/api-reachability.md`.
