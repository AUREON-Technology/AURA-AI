# ADR-041 — The agent task gateway, its queue, idempotent replay, voice-to-task, workspace lifecycle, and the usage and run-history APIs

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust
channels, confirmation), ADR-011 (desktop engineer agent), ADR-013/014 (engineer workspaces),
ADR-029 (service principals) · **Inventory rows:** 71, 72, 84, 87, 88, 89, 91 (backend and API
only; the client consumes these later)

## Context

An agent turn was only ever a `POST /chat/stream` the client held open. Seven gaps followed from
that, and the client team needs one contract for all of them:

- **84 remote task gateway.** A task had no id, no status and no result of its own. A dropped
  connection lost the turn.
- **87 task queue.** `llm/admission.py` queues individual *generations* inside the router. Nothing
  queued *tasks* (whole agent turns), so a person's backlog was not bounded, ordered or fair.
- **91 offline sync (backend half).** An offline client replays queued submissions. Nothing made
  a replay safe: a repeated submission ran twice, and a repeated approval was a 409 on a spent
  one-shot id.
- **88 voice to task.** `/voice/transcribe` returns text. Nothing turned a spoken request into
  something a task can start from, or asked about what was missing.
- **89 remote environment manager.** `WorkspaceManager` pauses and opens workspaces inside a turn;
  the person who owns one could not start, stop or inspect it.
- **71 task history.** A stored run keeps a digest of its goal, so it cannot be searched, and there
  is no timeline.
- **72 usage and cost.** Tokens and cost are computed per turn and exported as metrics, but not
  per user and not readable.

The constraints that shaped every choice: **no new table** (Alembic is the only schema authority
and a new table needs a real-Postgres round trip), **no new dependency**, **additive** (nothing an
existing endpoint does changes), **every new route is owner-scoped**, and **`AIKernel` stays the
only execution path**.

## Decision

### 1. A task is a `/chat` turn that runs server-side (rows 84, 87)

`POST /api/v3/agents/tasks` validates a request with **the code `/chat` uses**, passed in from
`v3.py` rather than re-implemented:

| Check | Where it comes from |
| --- | --- |
| rate limit | `enforce_rate_limit(..., "chat")` (the same budget and cost as a chat turn) |
| capability grant, caller context, session id, team scope | `to_turn` (the very function `/chat` calls) |
| Engineer consent | `AIKernel.preflight` (new, public): the check the kernel makes at the start of a turn, made at submission so the person hears *then* (403 `consent_required`) |
| plan entitlement and the message reservation | `admit_turn`; the reservation is returned if the task produces no answer, as `/chat` does |
| per-user token allowance, tool policy, taint, confirmation gates | enforced by the kernel when the task runs, because it runs through `kernel.run` |

The validated `TurnRequest` is stored as the task's *spec* (identity, capability grant, persona,
flags, evidence) and run later as exactly that: the task runs **as its owner, with the capabilities
they held when they submitted it, never more**. Two things are deliberately not carried because they
are per-request: connector sign-ins (`X-Aura-Connector-Token`) and images/`response_schema`
(refused by the route with a 422). `run_at` may be at most `AURA_AGENT_TASKS_MAX_SCHEDULE_AHEAD_S`
away (24 h), which also bounds how stale the stored grant can get.

`AgentTaskQueue` (`aura_core/tasks/queue.py`) decides *when* a task may start and hands it to
`KernelTaskRunner`, which calls `kernel.run`:

- **Priority** `high`/`normal`/`low`, with **aging**: a waiting task rises one level per
  `AURA_AGENT_TASKS_AGING_S`, so `low` is not starved.
- **Fairness**: among equal priority the user running the fewest tasks goes first, then arrival
  order.
- **Caps**: `max_concurrent` overall and `max_concurrent_per_user` running; `max_queued` and
  `max_queued_per_user` waiting. Past a waiting bound `POST` answers **429 `task_queue_full`** with
  `Retry-After` and creates nothing (its idempotency key is released).
- **`run_at`**: invisible to the picker until its time; a past time means now.
- **Lifecycle**: started in `Container.startup()`, stopped first in `Container.shutdown()`. `stop`
  cancels running work and marks every task it holds `failed` / `server_shutdown`
  (`retryable: true`), so none is left reading `running` for a process that is gone.
- **Heartbeat**: the owning process refreshes `heartbeat_at` for tasks it holds. A queued or
  running task whose heartbeat is older than `AURA_AGENT_TASKS_STALE_AFTER_S` is *reported* as
  `failed` / `interrupted` (`retryable: true`) by any worker that reads it. This is how a crash
  shows up, since a crash cannot mark anything.
- **Cancel across workers**: a cancel that reaches a worker that is not running the task flags the
  shared record; the owner's heartbeat loop stops the work within `heartbeat_s`.

**Where state lives.** Task records, specs, a per-owner index and idempotency claims are in the
**cache port** (Redis in production), under keys that carry a digest of the owner, TTL'd
(`AURA_AGENT_TASKS_RECORD_TTL_S`, 7 days). The index is a counter plus numbered slots (`incr` is
atomic across workers; a read-modify-write list would lose entries), so `GET /agents/tasks` lists
the newest 200 tasks per user.

**What a dropped connection does not lose, and what a restart does.** The task runs inside the
gateway process; the connection that submitted it is not involved, and the record is shared, so any
worker can answer a poll. A **server restart or crash does** lose queued and running tasks: they
read `failed` / `interrupted` and the client resubmits. Surviving a restart needs a durable job
table, which is a migration this change was told not to make (see "Not yet proven").

### 2. Idempotent replay (row 91)

`POST /agents/tasks` and `POST /agents/tasks/{id}/approve` accept a client-generated key as
`client_request_id` (alias `idempotency_key`) in the body **or** an `Idempotency-Key` header; the
three are the same key and must agree if more than one is sent (else 422). 1–255 printable ASCII
characters.

| Situation | Result |
| --- | --- |
| first request with this key | processed normally: `202`, `idempotent_replay: false` |
| same key, same person, same request | `200`, the **same task** (`task.id`), `idempotent_replay: true`, header `Idempotent-Replayed: true`; nothing runs again, nothing is metered, no rate-limit charge |
| same key, same person, **different request** | `409 idempotency_key_reused`, not retryable |
| same key arrives while the first is still being accepted | waits up to 2 s for it; else `409 idempotency_in_progress`, retryable: send the same request again |
| the first attempt was refused (422, 403, 429, queue full) | the key is **released**: fixing the request and resending under the same key works |
| same key, **different person** | unrelated: keys are scoped by owner |

"Same request" is a SHA-256 of the canonical validated body without the key fields (for approval:
the sorted set of approval ids). Keys are remembered for `AURA_AGENT_TASKS_IDEMPOTENCY_TTL_S` (24 h);
after that the key is new. The claim is an atomic `incr`, so concurrent retries across workers
create one task (tested with six at once).

**Approval.** A confirm-gated tool call parks the task in `awaiting_approval` with
`result.pending_tool_calls[]` (each with an `approval_id`). `POST /agents/tasks/{id}/approve
{approval_ids, client_request_id}` requeues it; the kernel resumes the *stored plan* and runs only
the approved calls, once (ADR-006). An approval id the task is not waiting on is `409
confirmation_invalid`; a task not awaiting approval is `409 task_not_awaiting_approval`. With a key,
an offline client's replay of the same approval returns the task (`200`) instead of the `409` a spent
one-shot id would otherwise give. A refused attempt releases the key.

### 3. Voice to task (row 88)

`POST /api/v3/voice/task` (`multipart/form-data` or urlencoded): exactly one of `file` (audio,
transcribed by the same helper `/voice/transcribe` uses) or `text`; optional `language` (`en`/`ar`)
and `refine` (default true). It returns:

```json
{ "goal": "fix the login bug in acme/web and open a PR",
  "constraints": ["don't touch the payments code"],
  "repository": "acme/web", "deliverable": "pull_request", "deliverable_text": "PR",
  "priority": "low",
  "ambiguities": [], "needs_clarification": false, "questions": [],
  "assumptions": [], "repository_hint": null, "code_task": true,
  "transcript": "...", "language": "en", "source": "deterministic",
  "refinement": {"requested": true, "status": "not_requested|applied|unavailable|invalid_output|disabled"},
  "suggested_request": {"goal": "...\nRepository: acme/web\nDeliverable: PR", "priority": "low",
                        "persona_hint": "engineer"},
  "started": false }
```

**It never starts anything** (`started` is always false); the client confirms with the person and
then calls `POST /agents/tasks`. **It does not guess.** A missing goal, a goal that is only a
pronoun, a code task with no repository, a repository named without its owner (`repository_hint`
keeps the name; `repository` stays null), two repositories, alternatives ("either ... or"), and a
work request with no stated deliverable each become an entry in `ambiguities` and a localized
(`en`/`ar`) entry in `questions`, and `needs_clarification` is true; `suggested_request` is null
until nothing needs asking. The only inferences made are recorded in `assumptions` (a question's
deliverable is an answer; "summarize" delivers a summary).

Stage one is deterministic (English and Arabic cues; `aura_core/voice/task_extractor.py`). Stage two
(`AURA_VOICE_TASK_REFINE`, default on) asks a model through the router for strict JSON, repairs
once, and otherwise keeps stage one. A model's output is **checked against the transcript before it
is believed** -- a repository, constraint or deliverable the person did not say is dropped -- and
`needs_clarification` is always recomputed from the merged fields, never taken from the model. The
call is unclassified, which the router treats as *sensitive*: a hosted provider never receives a
transcript (on a deployment with only a hosted provider the refinement reports `unavailable` and
stage one stands). The route has the same body ceiling and nginx locations as `/voice/transcribe`.

### 4. Workspace lifecycle and stats (row 89)

Owner-scoped `POST /engineering/workspaces/{id}/start|stop|restart` and `GET .../stats`, on
`WorkspaceManager` (`start` is `open()`, `stop` is `pause()`): they add no policy. `start` and
`restart` go through `open()`'s admission, so the running limit and the host-memory guard apply and a
refusal is `409 workspace_capacity` (`retryable: true`; a refused restart leaves the workspace
stopped). Starting may pause the least-recently-used workspace to fit; the response says how many
(`paused_to_make_room`) and which of *the caller's own* (`paused_yours`) -- never another person's
repository. `stats` never starts a stopped workspace; it reports `state`, `uptime_s`, `disk`
(`used_mb`, `quota_mb`, `percent`, `over_quota`) and CPU/memory from the runner's new
`workspace_stats` (one `stats` sample from the daemon, covered by the socket proxy's existing
`CONTAINERS` allowance, no exec), else `null` with `resources.reason`.

`GET /host/status` is read-only: whether the caller's paired desktop daemon is connected, whether it
allows control from other devices, and its granted folders. **Starting or stopping a person's desktop
remotely is impossible by design**: `aura_hostd` is their own process on their own computer and
connects out to the gateway; the server offering to spawn or kill software on someone's machine would
be remote control of a personal computer. `controllable_remotely` is always `false`, and a test
asserts no such route exists.

### 5. Run search and timeline (row 71)

`GET /agents/runs/search?q=&agent=&status=&since=&until=&limit=` and `GET /agents/runs/timeline`
(grouped by UTC day: runs, counts by status, plan steps, tool calls, iterations, tokens, elapsed
seconds, and the day's runs). Both scan the caller's newest `AURA_AGENT_RUNS_SEARCH_SCAN` (300) runs
and say so (`search_scope.scan_truncated`).

`q` is an AND of words matched against agent, status, stop reason, run id and the **names of the
tools the run called**. Goal text is *not stored* by default (the run store's content rule), so by
default it is not searchable, and `search_scope.goal_text_searchable: false` plus a note say so.
`AURA_AGENT_RUNS_STORE_PREVIEW` (default **off**) keeps a 200-character preview of each run's goal,
first passed through `SecretGuard` over the *whole* goal (so a key straddling the cut cannot leave
half behind), under an owner-digest key in the **cache**, TTL'd. The Postgres run store has no column
for it and the schema is Alembic's, so the limitation is documented: previews exist only for runs
made while the switch was on and only until their TTL.

### 6. Usage (row 72)

The kernel keeps one compact record per finished turn per user (turn id, time, persona, route,
outcome, provider, model, tokens in/out, cost, elapsed, cached, task id) in a bounded, TTL'd cache
log (newest `AURA_USAGE_LEDGER_MAX_PER_USER` = 5000 turns, 35 days). No message or answer text. A
failed turn that spent nothing is not kept, so a client retrying a refused request cannot push real
turns out of the window.

`GET /usage/summary?period=day|week|month&group_by=day|agent|task` -- totals and per-group totals
over the **trailing** 24 h / 7 d / 30 d (not calendar buckets); `truncated: true` means the window
did not reach back to the start of the period and the totals are a floor. `GET /usage/turns?limit=`
-- recent turns with their usage. `group_by=task` groups by the task id carried on the turn
(`(none)` for turns outside a task), so a task's spend is visible per task.

## API contract for the client team

All under `/api/v3`, bearer-authenticated like every v3 route, errors in the usual envelope
(`error`, `message`, `retryable`, `details`). A **service key** reaches none of these routes (the
route allowlist is closed by default).

**Task object**

```json
{ "id": "uuid", "goal": "...", "persona": "orchestrator", "status": "queued|running|awaiting_approval|succeeded|failed|cancelled",
  "priority": "high|normal|low", "created_at": "...", "updated_at": "...", "run_at": null,
  "started_at": null, "finished_at": null, "heartbeat_at": "...", "run_id": null, "turn_id": null,
  "correlation_id": "...", "session_id": null, "attempts": 1, "cancel_requested": false,
  "result": null, "error": null }
```

`result` (once there is one): `{message, summary, truncated, citations[], tools_used[],
needs_confirmation[], pending_tool_calls[], plan[], usage{prompt_tokens, completion_tokens, cost_usd},
provider, model, degraded, resolved_agent, agent_run_id}`. `error`: `{kind, message, retryable,
details}`; `kind` is the `/chat` error kind (`consent_required`, `user_quota_exhausted` with
`details.reset_at`, `safety_violation`, ...) or one of `timeout`, `interrupted`, `server_shutdown`,
`task_expired`, `internal_error`. `run_id` is for `GET /agents/runs/{run_id}`; it is known when the
turn finishes. `awaiting_approval` is an addition to the five statuses and means "needs a person",
not "failed".

| Route | Request | Response |
| --- | --- | --- |
| `POST /agents/tasks` | `{goal, persona?, priority?, run_at?, session_id?, use_knowledge?, use_memory?, use_tools?, plan?, strategy?, routing_policy?, require_local?, max_output_tokens?, temperature?, desktop_task_isolation?, workspace_root?, context?[], client_request_id?}`; unknown fields are 422 | `202 {task, queue{position,...}, idempotent_replay:false}`; `200 {..., idempotent_replay:true}` on replay; `402 plan_limit`, `403 consent_required`, `409 idempotency_*`, `422`, `429 rate_limited / task_queue_full` (+`Retry-After`), `503` if disabled |
| `GET /agents/tasks?status=&persona=&since=&limit=` | | `{tasks[], count}` -- rows carry `result_summary`, not `result` |
| `GET /agents/tasks/{id}` | | `{task, queue{position,running,queued,...}}`; `404` for an id that is not the caller's |
| `POST /agents/tasks/{id}/cancel` | | `{task, cancel{accepted, note}}`; idempotent; a finished task stays as it is |
| `POST /agents/tasks/{id}/approve` | `{approval_ids[], client_request_id?}` | `202 {task (queued), ...}`; `409 confirmation_invalid / task_not_awaiting_approval / idempotency_*` |
| `POST /voice/task` | form: `file` xor `text`, `language?`, `refine?` | the structured task above |
| `POST /engineering/workspaces/{id}/start\|stop\|restart` | | `{workspace, action, changed, paused_to_make_room, paused_yours[]}`; `409 workspace_capacity` |
| `GET /engineering/workspaces/{id}/stats` | | `{workspace, state, running, uptime_s, disk{}, cpu{percent}, memory{used_mb,limit_mb}, resources{available,reason}, capacity{}}` |
| `GET /host/status` | | `{connected, daemons[{kind,connected,remote_control,roots[],reachable}], controllable_remotely:false, note}` |
| `GET /agents/runs/search`, `/agents/runs/timeline` | see section 5 | `{runs[]\|days[], search_scope{...}}` |
| `GET /usage/summary`, `/usage/turns` | see section 6 | `{totals, groups[], truncated, ...}` / `{turns[]}` |

**What the client should do.** Generate a UUID per *user intent* and send it as
`client_request_id`; keep it with the queued submission and reuse it on every replay. Poll `GET
/agents/tasks/{id}` (a 2-5 s interval is fine; `queue.position` and `status` are enough for a progress
view). On `failed` with `error.retryable: true` and `kind` `interrupted`/`server_shutdown`/`timeout`,
offer a retry under a **new** key (the old key returns the failed task). On `429` honour
`Retry-After`. On `awaiting_approval`, show `result.pending_tool_calls[]` (tool, arguments,
`diff_preview`), then `approve`.

## Alternatives rejected

- **A durable job table now.** The right home for tasks that survive a restart, and the first thing
  to build when that is needed; but a new table needs a migration with a working downgrade and a
  real-Postgres round trip, which this change was explicitly not allowed to attempt.
- **Reusing `Idempotency-Key` alone (the existing `build_idempotency` guard).** It replays a stored
  *response* and answers a reused key with 422; the contract here is "same key, same task", a 409
  for a changed request, and the body field the offline sync layer wants. It also could not make
  approval idempotent, because a spent approval fails inside the kernel.
- **Running tasks in a Celery worker.** A worker would need the whole request, including the
  capability grant, serialised through a broker, and would add a second place that decides when a
  user's work may start. The in-process queue reuses `kernel.run` unchanged.
- **Storing the goal text on the run for search.** Breaks the run store's content rule for
  everyone to help the few who search; a separate, opt-in, redacted, expiring preview does not.
- **Letting the model's judgement decide `needs_clarification`.** It would let a plausible-sounding
  model paper over a missing repository. Recomputing it from checked fields keeps "ask, don't
  guess" a property of the code.
- **Remote start/stop of the desktop daemon.** See section 4.

## Consequences

- Tasks do not survive a server restart (they fail as `interrupted`/`server_shutdown`, retryably).
- Queue and concurrency limits are **per gateway process**; with N workers the effective ceilings are
  N times the setting. The shared record is what lets any worker answer a poll.
- Task records, usage records, idempotency claims and previews share the cache with the LLM cache. On
  Redis that is fine; on the in-memory cache (single process, 4096 entries, LRU) a busy LLM cache can
  evict them early. Production uses Redis.
- A task's goal and result text sit in the cache for `AURA_AGENT_TASKS_RECORD_TTL_S` (7 days), owner
  keyed. That is the cost of "the result is still there when the client comes back".
- Usage totals are floors when `truncated` is true; a person with more than 5000 turns in a month
  sees a partial month.
- The Postgres engineer/agent run store is untouched.

## Not yet proven

Built and tested on the in-memory substrate (the default suite, the real kernel, the real queue and
store, the scripted echo provider). **Nothing here has run against Redis, a real Docker daemon, a
real STT model or a live deployment.** Specifically unproven: the cache-log and idempotency-claim
behaviour under real Redis concurrency across workers (the `incr` atomicity is Redis's, the test of
it is a thread pool on one process); the Docker `stats` reader against a real daemon (it is tested
against a stand-in for the client); `/voice/task` audio through the nginx location on the VPS
(pinned by the config test, not exercised); the model-refinement stage against a real model; and
that a client built to this contract behaves. The client UI for rows 71, 72 and 91 does not exist.
