# AURA Upgrade Roadmap

Baselined against `8286b1a` on 2026-08-22. Supersedes the phase table in
[`Doc/V2_BLUEPRINT.md`](../Doc/V2_BLUEPRINT.md) §13 and the forward-looking half of
[`docs/analysis/03-missing-components-report.md`](analysis/03-missing-components-report.md).
Reads as a successor to [`AURA_AI_PRODUCTION_READINESS_SCORE.md`](../AURA_AI_PRODUCTION_READINESS_SCORE.md),
whose scoring discipline it adopts.

## 0. Purpose

This roadmap exists because the most recent strategy proposal for AURA — a 37-point plan to grow
the system into an "AI operating system" — was written against a stale baseline. Its strategic
thesis is right and is adopted here in full. Its factual premises about what is and is not built
are, in eight specific places, no longer true, and roughly its first three phases describe work
that has already merged.

The standing rule for this document, inherited from the readiness score:

> A phase may not be marked complete without the evidence its exit criteria name. "Built" and
> "tested" are not "proven". Where a number appears, the conditions it was measured under appear
> with it.

## 1. Baseline correction

Every row below was checked against the working tree at `8286b1a`. This section is the reason the
rest of the document can be trusted; if it drifts, re-verify before planning against it.

| Earlier claim | Verified state |
| --- | --- |
| `AIKernel.stream()` plans but does not execute tools | **False.** `aura_core/kernel/kernel.py:437` calls `_should_plan`, then `_iter_execution`, yielding `step` frames and an `ExecutionOutcome`. Model-emitted `tool_calls` frames stay informational by design — planning/execution is the only execute path (ADR-006). |
| `InMemoryGraphStore()` is hardcoded and lost on restart | **False.** `aura_core/container.py:422` returns `PostgresGraphStore` whenever `database_url` is configured. |
| `InMemoryAuditSink` is unsuitable for production | **Addressed.** `aura_core/container.py:441` returns `PostgresAuditSink`. The in-memory sink is the no-database fallback. |
| The documented 02:00 knowledge refresh is not actually scheduled | **False.** `aura_backend/celery_app.py:82` schedules it (default `0 2 * * *`, overridable via `AURA_LEARNING_REFRESH_CRON`), alongside memory consolidation at 02:30 and hourly insight generation. |
| The client still uses v1 for memory, RAG, learning, and fallback chat | **Substantially false.** Every client repository targets `/api/v3/*` — chat, memory, knowledge, sync, auth, insights, learning, automation. One dead seam remains; see Phase B. |
| v1/v2/v3 fragmentation is the systemic problem | **Largely resolved.** `aura_service.py` is a v3-only gateway that never imports `main.py`; the unversioned and v1 paths are unmounted and asserted 404 by `tests/unit/test_v1_locked_routes.py`. `api/v2/{auth,sync,insights}` survive deliberately as platform adapters outside the core (ADR-002). |
| P0 security blockers AURA-001…AURA-017 are open | **Closed** in `9cb6e8f`, `5f4200c`, `61fafde`. `allow_mock_attestation` is refused in staging/production (`aura_core/config/settings.py:740`), `client_ai` is gone, release signing is env-driven. |
| Planner, context engine, memory taxonomy, hybrid RAG, tool registry, evaluation harness are missing | **All exist**, under `aura_core/{planning,context,memory,rag,knowledge,tools,evaluation}/`. |
| `pip-audit` should be added to CI | **Already there** — `.github/workflows/backend-ci.yml:96` (AURA-020). |

### What is genuinely still open

- **Agents are prompts, not agents.** `aura_core/prompts/library.py:402` defines seven entries in
  `AGENT_PROMPT_IDS`. There is no registry carrying per-agent tools, capabilities, risk ceilings,
  or state.
- **Tools can only propose.** `aura_core/tools/personal_os.py` exposes `propose_task`,
  `propose_event`, `propose_expense`, `propose_income`, `propose_health_log`; the write happens
  client-side after approval. `builtin.py` adds calculator, HTTP fetch, and weather. No
  server-side CRUD tool exists.
- **No Projects or Goals**, in either `aura_backend/infrastructure/db/models.py` or
  `aura_client/lib/core/database/database.dart`.
- **Proactivity is rule-based.** `aura_backend/services/insights.py` is fixed rules by deliberate
  choice; `InMemoryEventBus` (`aura_core/adapters/runtime.py:33`) documents itself as at-most-once
  and non-durable.
- ~~No OCR, no vision, no voice.~~ **Partly closed by Phase F.** OCR and self-hosted STT/TTS are
  real and wired; no vision model exists, and video has no extraction at all.
- **No evidence.** No labelled evaluation suite exists. No `tests/load/` exists. The 4.56 ms p95
  and 478 turns/s figures describe in-memory adapters and an echo provider. The production compose
  file has been deployed zero times.

## 2. Standing architectural principle

Adopted from the strategy proposal, and binding on every phase below:

> AURA is not a chatbot with productivity features. It is one intelligence core — understanding,
> memory, planning, tool execution, observation, learning — of which conversation is a single
> interface.

Operationally this means new capability goes *through* the kernel, not beside it. The existing
ADRs already encode most of what that requires, and no phase below may weaken them:

- **[ADR-002](architecture/002-strangler-migration.md)** — `aura_core` does not import `services/`.
  New adapters attach at `aura_service.py`, not inside the core.
- **[ADR-006](architecture/006-safety-pipeline.md)** — planning/execution is the only path that
  executes a tool. Dispatching model-emitted tool calls directly would bypass taint tracking and
  confirmation.
- **[ADR-008](architecture/008-human-in-the-loop-learning.md)** — no fine-tune is promoted without
  human approval. Index promotion stays gated on a recall comparison.
- **[ADR-009](architecture/009-observability-first.md)** — telemetry is a return value, not a side
  effect, so it cannot be forgotten.

## 3. Phases

### Phase A — Evidence

**Why now.** The readiness report identifies three actions that each move more than one dimension,
and nothing else that competes with them. Every capability in Phases B–G is unverifiable until
these land, and any performance decision taken before them is guesswork. This phase adds no
features.

| # | Work | Reuse |
| --- | --- | --- |
| A1 | Deploy `deploy/docker-compose.prod.yml` for real. **Done.** The full 17-service stack (`prometheus`, `grafana`, `node-exporter`, `promtail`, `qdrant`, `postgres`, `redis`, `worker-ingestion`, `alertmanager`, `ollama`, `beat`, `loki`, `worker-realtime`, `backup`, `gateway`, `console`, `nginx`) is running on the Contabo VPS and has been serving `169.58.188.43.nip.io` over real HTTPS for multiple days. Deploy VPS run #27 (2026-08-23, commit `8e9bc6fc`) is the CI pipeline's first-ever success after 26 straight failures: it pulled, rebuilt the gateway and console images, ran `alembic upgrade head`, restarted the stack, and both `/health` and `/health/ready` verified OK against the live domain — closing the loop this phase asked for. See [phase-a1-first-boot.md](reports/phase-a1-first-boot.md) for the sandbox groundwork that found the four defects fixed along the way (#20, #21, #25, #26). | `deploy/scripts/bootstrap-host.sh`, `bootstrap-secrets.sh`, `vps-update.sh` |
| A2 | Execute one restore drill against a staging copy. **Done** — the drill's first execution on 2026-08-23 failed on a defect that made it unable to succeed at all; fixed, and its assertion tightened to check rows rather than table count. The `backup` service was then run for the first time and works as written; Postgres, Qdrant and Redis were all restored from its output. See [phase-a2-restore-drill.md](reports/phase-a2-restore-drill.md). The offsite `rclone` path and `restore.sh` remain unexercised. | `deploy/scripts/restore-drill.sh`, `backup.sh` |
| A3 | Build a labelled retrieval/answer corpus and gate CI on it. **Retrieval half done.** A 10-document corpus (the repo's own `docs/architecture/*.md`) with 23 graded queries now gates every PR through `pytest tests` — no workflow edit needed, `testpaths` already discovers `tests/e2e/test_docs_corpus_gate.py`. The baseline is real and unflattering (recall@5 = 0.65, pass_rate = 0.61) because the default `HashEmbeddingProvider` is non-semantic; published as measured, not tuned to pass. See [phase-a3-retrieval-corpus.md](reports/phase-a3-retrieval-corpus.md). **Still open:** answer/citation/groundedness gating (`AnswerEvaluator` needs a real or scripted provider — `EchoProvider`'s default echoes the user's own message) and per-RAG-strategy comparison across `aura_core/rag/{adaptive,agentic,base,expansion,leaves}/`. | `EvalSuite.from_jsonl` (`aura_core/evaluation/harness.py:77`), `RetrievalEvaluator`, `RegressionGate` — no new metrics were written. |
| A4 | Load test with the real data tier. **Done.** `aura_backend/tests/load/test_real_data_tier.py` boots the real `Container` — Postgres, Qdrant, Redis, Ollama — and measures single-turn latency and a concurrency ceiling with `qwen2.5:3b-instruct-q4_K_M` in the path. Its first run found a real, previously-undetected defect: `IndexIdentity.matches_embedding` compared a raw embedder id against one recovered through a lossy slug round-trip, so retrieval silently returned nothing against production's real `sentence-transformers` embedder on every query — invisible to every prior test because they all use `HashEmbeddingProvider`, whose id happens to already be its own slug. Fixed (`aura_core/domain/knowledge.py`) and regression-tested before the numbers below were captured. p95 latency **70.5 s** (CPU-only, 3-core sandbox — not comparable to a GPU deployment). Concurrency throughput is **flat from 1 to 8 concurrent requests** (`OLLAMA_NUM_PARALLEL=1` serializes every generation), confirming the concurrency ceiling the compose file's own comments already asserted. See [phase-a4-load-test.md](reports/phase-a4-load-test.md). | Extend the harness in `tests/benchmarks/bench_pipeline.py` — its conditions, not its numbers. |
| A5 | `pip-audit` in CI. **Already done before this roadmap existed** — `.github/workflows/backend-ci.yml`'s `pip-audit` job (AURA-020); see §1's baseline-correction table. Listed here only so the phase table is complete. | — |

**Exit criteria.** A real recall@10 / nDCG / groundedness figure per RAG strategy, on a real
corpus — **partial**: recall/nDCG/MRR measured for the default hybrid strategy only (A3); per-
strategy breakdown and groundedness gating remain open. A real p95 with a model in the path —
**done**, 70.5 s (A4). A documented concurrency ceiling naming `OLLAMA_NUM_PARALLEL` as the
binding constraint — **done**, flat throughput 1→8 concurrent (A4). One successful restore —
**done** (A2).

**Non-goal.** Tuning. Phase A measures; Phase G optimises against what it measured.

### Phase B — Close the last legacy seams

Small, and it removes ambiguity that Phase C would otherwise inherit. **Done**, 2026-08-24.

- **B1. Done.** Deleted `aura_backend/main.py` — confirmed unused by `aura_service.py` (which
  never imported it) before removal, full test suites (pytest + legacy unittest) green after.
  Its removal orphaned `api/v2/chat.py` (`main.py` was its only caller), deleted with it. The
  dead client path at `online_agent_manager.dart` had already been retired to a stub that always
  throws rather than actually calling `/api/chat/agent` — nothing imported it, so the whole file
  was deleted rather than left as an inert stub. `migration-guide.md` corrected: it previously
  described `main.py` as still mounting `/api/v3` and a `create_app(standalone=True)` parameter
  that does not exist in the current signature.
- **B2. Investigated, corrected, and fixed the real bug — not the one originally described.**
  The premise was stale: `UserMemories` was never stuck on a bespoke protocol needing to move to
  `/api/v3/sync`'s cursor semantics — `core/sync/sync_client.dart`'s own docstring already
  documents deliberately keeping memories off that CRUD-row protocol, because memory data is
  embedding-shaped (kind, decay, provenance) and already has its own purpose-built kernel route,
  `/api/v3/memory` (`aura_core/api/v3.py`), which `MemoryRepository` already correctly calls.
  Moving it onto the generic sync protocol would have been a regression, not a fix. What
  investigation found instead: `MemoryRepository.syncMemories()` wipes the entire local memory
  cache and refetches with no `limit` param, silently falling through to the route's own default
  of 20 — any user with more than 20 memories lost the rest from local view on every sync. Fixed
  by requesting the route's actual maximum (`limit=100`), with a regression test. 100 remains a
  hard ceiling, not real pagination; a user past that count still loses the tail — named as
  follow-up, not fixed here, by explicit scope decision. Also found and left as follow-up: no
  outbox-backed retry for offline memory writes/deletes (unlike Tasks/Messages), and memory
  content is not encrypted client-side before transmission the way task/message content is.
- **B3. Done.** Recorded in [ADR-002's 2026-08-24 update](architecture/002-strangler-migration.md#update--2026-08-24-phase-b1b3):
  `api/v2/{auth,sync,insights}` are not legacy code awaiting removal — every one of their routers
  already declares `APIRouter(prefix="/api/v3/...")`, so by URL they already are `/api/v3`. The
  `v2` in the directory path is a stale name, not a live API version. Decision: the architectural
  boundary (direct SQLAlchemy, outside `aura_core`'s hexagonal ports) is **permanent**, for the
  same reason ADR-002 already gives for not rewriting `services/` — the code is correct and
  efficient as written, and rewriting it would be churn. Phase C's new endpoints go through
  `aura_core`'s kernel/tool path, not this layer. Renaming `api/v2/` to stop the name overstating
  its legacy status is named as a correct, low-risk, non-gating follow-up.

### Phase C — Agent platform

**Done**, 2026-08-24.

- **C1. Done.** `aura_core/agents/registry.py`'s `AgentSpec`/`AgentRegistry` replace
  `AGENT_PROMPT_IDS`. Confirmed by reading the kernel before touching it: every one of the seven
  personas previously shared the exact same tool universe — `agent` only selected a system prompt
  template, `_tool_declarations()` filtered solely by the caller's own capabilities, never by
  which persona was asking. Now each `AgentSpec` carries `allowed_tools`, `required_capabilities`,
  a `risk_ceiling` (`ToolSensitivity`), and a `memory_scope`, and the grant is enforced through the
  *existing* machinery, not a new one: `allowed_tools`/`risk_ceiling` ride through
  `ToolContext` into `ToolPolicy.evaluate()`, which gained one new rule (`outside_agent_grant`)
  denying and auditing a call outside the resolved agent's set — the identical
  `_audit_call` → `AuditSink.record` path every other policy refusal already uses.
  `GET /api/v3/agents` now returns each persona's real grant, not just its prompt id. 14 new
  tests, including one that asserts every persona's grant is a real, registered tool name (catches
  drift between the registry and `tools/`).
- **C2. Done, with a corrected scope.** Investigated before implementing (per the roadmap's own
  standing rule) and found the literal wording — CRUD against the `tasks`/`calendar_events`/
  `finance_records`/`health_records` Postgres tables — describes something that cannot be built
  safely: those tables are zero-knowledge encrypted client-side
  (`aura_client/lib/core/security/encryption_service.dart`), and the server holds no key to
  produce valid ciphertext with. Writing plaintext into a column the whole architecture promises
  never contains any would not degrade that guarantee, it would invert it. Raised with the user
  mid-task; decision: build the CRUD tools against a new, aura_core-owned table instead
  (`aura_personal_os_records`, via `PostgresPersonalOsStore` — same raw-asyncpg pattern
  `aura_memories`/`aura_documents` already use, sharing the connection pool), leaving the legacy
  encrypted tables and their guarantee completely untouched. Eight new tools
  (`create_task`/`list_tasks` and the calendar/finance/health equivalents,
  `aura_core/tools/records.py`) reach real, persisted, listable storage the kernel itself can read
  back — proven end to end against a real (throwaway) Postgres instance, not only the in-memory
  adapter. **Still open, named rather than silently dropped:** update/delete tools (create+list
  only so far); syncing these new records to the Flutter client's existing UI (they are currently
  server-visible only — no new client work was in this PR's scope).

**Exit criteria.** An agent's tool set is data, not code — **done** (C1). A tool call outside an
agent's grant fails closed and is visible in the audit sink — **done**, proven against both a
synthetic tool (C1) and a real C2 tool (`create_health_record` denied for `finance_advisor`).
Task-completion rate is measurable through the Phase A harness — **still open**: the harness has
no task-completion evaluator today, and building one is a Phase-A-shaped follow-up, not implied by
having something to measure.

### Phase D — Projects, Goals, and prioritisation

**Backend done**, 2026-08-24. Client work explicitly deferred — see below.

The deliberately scoped-down Business OS. These two domains earn their place because the kernel
needs them to answer "what should I work on"; leads and revenue do not (see §4).

- **D1. Backend done; Flutter client deferred.** `Project` and `Goal` added to
  `infrastructure/db/models.py` with `SyncableMixin`, and to the `SYNCABLE_MODELS` dict that
  drives `api/v2/sync.py`'s generic cursor pull/push — confirmed no other change to `sync.py` was
  needed, exactly as its own reflection-based design promises. New migration
  `alembic/versions/0004_projects_and_goals.py` creates both tables (`goals.project_id` is a
  nullable FK to `projects.id` with `ON DELETE SET NULL`, so archiving a project never deletes a
  goal) and was verified end to end against a real (throwaway) Postgres instance: applied,
  downgraded, and re-applied cleanly; an ORM round trip confirmed every column including the FK;
  a live delete of the parent row confirmed the child's `project_id` actually becomes `NULL`, not
  just that the migration's SQL text says it should. Column split follows every other syncable
  table exactly — `title_enc`/`description_enc` ciphertext the backend cannot read, plaintext
  `status`/`priority`/`category`/`target_date`/`progress_pct` it can. **Still open, named rather
  than silently dropped:** the Flutter half (Drift tables in
  `aura_client/lib/core/database/database.dart`, feature modules under `features/projects/` and
  `features/goals/` following the `features/tasks/` layout). This sandbox has no Flutter/Dart
  toolchain and insufficient disk headroom for one (`database.g.dart` is 8,000+ generated lines
  that cannot be hand-edited safely), so the schema and sync protocol are ready and waiting but no
  device can write to them yet. A session with real Flutter tooling is a direct follow-up, not a
  redesign.
- **D2. Done.** `aura_core/domain/goals.py`'s `GoalSnapshot` carries only the plaintext columns
  above — no title or description, because those are the same zero-knowledge ciphertext D1
  couldn't touch either. `aura_core/adapters/postgres_goals.py`'s `PostgresGoalsPort` reads the
  legacy `goals` table directly (raw `asyncpg`, joined through `users.auth_subject` since
  `UserId` is the auth-subject claim, not the table's internal UUID) — it never issues
  `CREATE TABLE`, unlike every other `postgres_*.py` adapter, because this table is owned by D1's
  migration, not by this port. `aura_core/kernel/kernel.py`'s `_gather_context` fetches active
  goals concurrently with memory recall, retrieval, and history load (the same
  `asyncio.gather` that already made those three concurrent); `ContextEngine._goal_items` renders
  each as a `MEMORY`-section item ("persistent facts about the user", per that section's own
  ordering rationale) such as `Active goal (priority=high, category=fitness, target_date=2026-09-01,
  progress=40%)`. `InMemoryGoalsPort` backs the default in-memory container and test suite.
  18 new tests across `test_context_goals.py` and `test_kernel_goals.py`, including a full-turn
  test that a seeded goal does not break the ordinary pipeline.
- **D3. Done.** Two new registry tools in `aura_core/tools/planning.py`, both pure computation —
  caller supplies task/goal/busy-interval descriptors as tool arguments (gathered via
  `list_tasks`/`list_calendar_events` or conversation context); neither reads or writes any store,
  so neither needed a port or a capability grant beyond the tool itself. `prioritize_tasks` scores
  each task by deadline urgency (overdue outranks due-soon), goal-priority alignment, a small
  quick-win tilt for lower effort, and a bonus for unblocking other listed tasks; a task whose
  `depends_on` names another task id still present in the list is held out of the ranking and
  reported separately as blocked, rather than mis-scored. `find_calendar_slots` merges supplied
  busy intervals and walks the search window (optionally confined to daily working hours) for
  gaps of the requested length, capped at 400 search days to bound the scan the same way
  `calculator`'s exponent cap bounds arithmetic. Granted to `task_planner` alongside the existing
  task/calendar tools. 14 new tests in `test_planning_tools.py`.

### Phase E — Proactive intelligence

**Done**, 2026-08-24.

Investigated before implementing (per the roadmap's own standing rule) and found a real
discrepancy between this codebase's own docstring and its behaviour: `InMemoryEventBus`'s module
docstring claims "audit logging, metric updates, and cache invalidation subscribe to domain
events." A full-repo search for `.subscribe(`/`.subscribe_all(` outside that file itself and its
own test fixture returned nothing — every engine publishes to the bus, nothing has ever consumed
it. Audit writes happen through direct `audit.record(...)` calls threaded through each engine
independently. That reframes what "proactive intelligence" means here: it is not upgrading an
existing reactive layer, it is building this system's first one.

- **E1. Done.** A durable outbox for event types a live handler failure must not lose
  (`aura_core/adapters/postgres_outbox.py`'s `PostgresEventOutbox`, `aura_event_outbox` table,
  `FOR UPDATE SKIP LOCKED` + a `claimed_at` lease so a crashed drain worker's claim expires rather
  than stranding the row forever). `DurableEventBus` (`aura_core/adapters/runtime.py`) wraps the
  existing `InMemoryEventBus` unchanged for every non-durable event type; a durable-marked type is
  enqueued instead of live-dispatched, so there is exactly one delivery path per event, not two
  racing ones. Drained by `tasks.drain_event_outbox_task`, scheduled every 60s (`celery_app.py`) —
  `aura_core/learning/jobs.py`'s `run_outbox_drain`. Verified end to end against a real (throwaway)
  Postgres instance: concurrent `claim_batch` calls never double-claim the same row, a
  dead-lettered entry (5 failed attempts) is never reclaimed, and `PostgresInsightSink.surface`'s
  `ON CONFLICT DO NOTHING` makes a redelivered event's action a true no-op, not a duplicate.
  Found and fixed a real bug while building this: `DomainEvent.as_dict()` used
  `dataclasses.asdict()`, which recursively flattens every nested dataclass — including each
  identifier field, itself a frozen dataclass — into a plain `{"value": ...}` dict *before* the
  method's own flattening check ever ran, silently defeating it for every event with an identifier
  field. Nothing had round-tripped an event through `as_dict()` before this phase, so nothing had
  ever exercised the bug.
- **E2. Done.** `aura_core/triggers/`: `TriggerEngine` is the event bus's first real subscriber,
  wired via `subscribe_all` in `container.py` (so future non-durable, low-latency triggers work
  without new plumbing) and, for durable types, via the outbox drain. One real trigger ships:
  `document_quarantined` reacts to `DocumentIngested(quarantined=True)` — genuinely published
  today (`aura_core/knowledge/engine.py`, both the fully- and partially-quarantined ingestion
  paths) and, per the same subscriber search above, never previously consumed by anything. A
  document a user uploaded could be silently short of the content they expect with no record
  anywhere saying so; the trigger surfaces a per-user `Insight` (`kind="document_quarantined"`)
  through the same `InsightSink` port the daily briefing uses. The daily briefing itself
  (`aura_core/triggers/briefing.py`'s pure `build_daily_briefing`, same caller-supplies-the-data
  design as Phase D3's tools; `services/daily_briefing.py` supplies real counts from Postgres) runs
  once a day via `tasks.generate_daily_briefings_task` and writes an ordinary `kind="daily_briefing"`
  insight — no client change needed, it renders on the existing Today-screen feed
  `services/insights.py` already populates. 46 new tests, plus a container-wiring integration test
  proving the whole pipeline (publish → outbox → drain → decode → trigger → surfaced insight)
  end to end on the in-memory stack the full suite runs on. (38 tests across the four new files,
  5 more added to `test_beat_schedule.py` for the drain job and its beat entry.)
- **E3. Corrected scope, done within it.** The roadmap's own text sets the bar precisely: "the
  burden of proof is on the replacement," proof meaning the Phase A eval harness
  (`aura_core/evaluation/harness.py`'s `AnswerEvaluator`/`RegressionGate`) shows no regression
  against a real model. That harness scores free-text kernel answers against a query; this
  environment has no real (non-echo) model to generate an answer worth scoring — Phase A4's real
  load test required standing up a live Ollama container with a pulled model, infrastructure this
  session did not reconstruct for one eval run. Building and shipping a model-generated replacement
  without that proof would be exactly the thing the roadmap's own gate exists to prevent, so the
  rule engine stays the production insight generator. What *is* real and shipped: every rule-based
  candidate (`services/insights.py`'s `overdue_tasks`/`hydration`/`high_spend`, and the new
  `daily_briefing`) now carries `citations` — the actual record ids (task/health/finance/goal/event)
  that produced it — alongside `message`/`explanation`, surfaced through `api/v3/insights`'s
  `InsightResponse.citations`. This is the grounding half of "insights with citations": a future
  model-phrased version needs the same evidence to cite from, and capturing it now costs no model
  call and is fully backward compatible (an unrecognised JSON key was already ignored by the
  client's `payload.get(...)` reads). Verified against a real Postgres via a new, permanent,
  CI-running integration test (`test_insights_integration.py`, the same testcontainers pattern as
  `test_sync_integration.py`) — which, while being written, found that pattern's pre-existing
  standalone-run fragility (it only passed as part of a full `unittest discover` run, which
  happened to import `infrastructure.db.models` via some earlier module first) and fixed it in both
  files with the missing explicit import.

**Exit criteria.** A domain event reaches a real reaction without being lost to a swallowed handler
exception or a mid-dispatch crash — **done**, proven against a real Postgres outbox with concurrent
claims and a dead-letter path. A proactive surface exists that nothing published before this phase
consumed — **done** (`document_quarantined`, `daily_briefing`). Kernel-generated insights with
citations, gated by the eval corpus — **citations done; generation correctly not attempted**
without the real-model eval infrastructure the gate requires to mean anything.

### Phase F — Multimodal and voice

**Done within corrected scope**, 2026-08-24.

Investigated first: `aura_core/ports/documents.py`'s `OcrEngine` protocol and
`aura_core/knowledge/documents.py`'s `extract_pdf`/`apply_ocr` pipeline were already fully built
and unit-tested (`test_document_extraction.py`) — page-aware, honest about what it cannot read
(`needs_ocr=True` plus a placeholder note naming the reason), and carrying image bytes when
Pillow is available. But a full search of `aura_core/api/v3.py` found **zero callers**: the only
existing `/knowledge/documents` route took plain-text `content: str` and never touched raw PDF
bytes. The pipeline was structurally dead from the API's perspective — the same "built but never
wired" shape as Phase E's event bus.

- **F1. Done, scope-corrected.** Added `TesseractOcrEngine`
  (`aura_core/adapters/tesseract_ocr.py`, `pytesseract` over the system `tesseract-ocr` binary,
  fails fast in its constructor rather than on first use) and wired it into a genuinely new
  endpoint, `POST /api/v3/knowledge/documents/upload` (multipart PDF), which is the first real
  caller `extract_pdf`/`apply_ocr` have ever had. `KnowledgeSettings.ocr_enabled`/`ocr_lang` gate
  it; `container.py` only builds the engine when both the setting and the binary are present, the
  same lazy/optional pattern as every other adapter. Proven end to end with no mocks: a
  Pillow-rendered scanned PDF (`Image.save(buf, format="PDF")` produces a real JPEG image
  XObject `pypdf` extracts and Tesseract opens) is uploaded through the full HTTP stack and its
  OCR'd text becomes genuinely searchable. **A vision model was not added.** OCR resolves the
  specific gap the readiness report names — scanned pages indexed as placeholders — but a vision
  model (describing a chart, reading a diagram) is a different capability with no self-hosted
  candidate verified in this environment, and per the same standard Phase E3 applied ("burden of
  proof is on the addition"), it is left out rather than added unproven. The `multimodal` RAG
  strategy now has real OCR'd text to retrieve from PDFs uploaded through the new endpoint; it
  still has no image-understanding signal, unchanged from before this phase.
- **F2. Done.** Built from scratch — a repo-wide search for whisper/STT/TTS returned nothing, so
  this is genuinely new infrastructure, not a rewire. `aura_core/ports/speech.py` defines
  `SpeechToText`/`TextToSpeech`; `FasterWhisperEngine` (`adapters/whisper_stt.py`, CPU-friendly
  CTranslate2 Whisper, `tiny.en` by default) loads its model lazily on first `transcribe()` call
  behind an `asyncio.Lock` — matching `PostgresPool`'s "every connection is lazy" discipline, so
  constructing the adapter never makes a network call. `EspeakTtsEngine`
  (`adapters/espeak_tts.py`) shells out to the system `espeak-ng` binary — chosen specifically
  because it needs no model weights and no extra pip dependencies, an explicit
  disk/CPU-budget tradeoff for a synthetic-sounding but fully self-hosted voice, documented as
  such in the adapter's own docstring. Both keep inference on our own infrastructure; no hosted
  vendor branch exists anywhere in `aura_core/llm/registry.py`, and none was added here either.
  Wired through `VoiceSettings` and two new endpoints, `POST /api/v3/voice/transcribe` and
  `POST /api/v3/voice/speak`, both returning 503 rather than silently degrading when the engine
  is unavailable. Proven with a real, no-mocks round trip: `espeak-ng` synthesises "one hundred
  dollars" to WAV, `faster-whisper` transcribes it back, at three levels — direct adapter calls,
  the wired `Container`, and full HTTP through `TestClient`.
- **F3. Done (client wiring), added after F1/F2 shipped.** Both endpoints landed with no client
  path to reach them: the Knowledge Center screen only had a paste-text form (no file picker
  dependency, no multipart request code anywhere in `KnowledgeCenter`), and there was no voice
  code in `aura_client` at all — no mic button, no recording package, no playback package, a
  gap confirmed by a full-repo search before writing anything. Closed both. `KnowledgeCenter`
  gained `uploadPdf()` (real `http.MultipartRequest`, not JSON) wired to an "Upload a PDF
  instead" button on the Knowledge Center screen (`file_picker`, PDF-filtered, `withData: true`
  so bytes are available on every platform, not just where a file path exists). A new
  `VoiceClient` wraps `/voice/transcribe` (multipart) and `/voice/speak` (JSON in, WAV bytes
  out); `VoiceRecorderButton` (mic, `record` package, WAV output to match what `faster-whisper`
  expects) sits next to the chat composer's send button and drops the transcript into the
  existing text field rather than auto-sending, so the user still reviews before it goes out;
  `VoicePlaybackButton` (speaker icon, `audioplayers`) sits on every non-streaming assistant
  reply. Microphone permission entries added for Android (`RECORD_AUDIO`), iOS and macOS
  (`NSMicrophoneUsageDescription`), and macOS's sandboxed entitlements
  (`com.apple.security.device.audio-input`). Unit tests added for both new API-client methods
  (`voice_client_test.dart`, `KnowledgeCenter.uploadPdf` cases in `knowledge_center_test.dart`)
  following the existing `MockClient` pattern. No Flutter toolchain exists in this environment —
  the same constraint every other client-only commit this session has carried — so this is
  reasoned through against the existing code's conventions and left for Client CI's `flutter
  analyze`/`flutter test`/Android build gate to confirm, not claimed as locally verified.
  Deliberately not built: an admin-scorecard screen for `GET /admin/scorecard` — the client has
  no admin-role concept anywhere in its auth model, and a live ops dashboard fits an internal
  tool more than the consumer app.

### Phase I — Transcript / text NLP

**Status: scaffolding / partial.** Design: [`docs/plans/phase-i-nlp.md`](plans/phase-i-nlp.md).

Stdlib-first `aura_core/nlp/` (`NlpEngine`) normalizes chat messages and STT transcripts before
`TurnRequest` construction; optional shallow intent + `RuleEntityExtractor` metadata (not written
to the graph). Wired through `container.nlp` and chat `to_turn`; live voice calls the same
`normalize_for_turn` extension point. **Not** a spaCy stack; no hosted NLP. Proven quality gains
on real ASR transcripts are still required before marking the phase done.

### Phase G — Continuous intelligence

**Done**, 2026-08-24.

- **G1. Done.** Before this phase, `KernelDependencies.telemetry_sink` was never wired anywhere in
  `container.py` — every turn built a complete `TurnTelemetry` record and nothing kept it past the
  request. Added `PostgresTelemetrySink` (`adapters/postgres_telemetry.py`, self-managed
  `aura_turn_telemetry` table, same `CREATE TABLE IF NOT EXISTS` pattern as
  `postgres_audit.py`/`postgres_outbox.py`) and `InMemoryTelemetrySink` as its in-memory twin, both
  behind the pre-existing `TelemetrySink` port. `GET /api/v3/admin/scorecard` combines
  `telemetry_sink.recent_summary(since=...)` — real p50/p95 via Postgres `percentile_cont`, error
  rate, cache-hit rate, tool success rate, average groundedness — with the static baselines Phase
  A3's `RegressionGate.save_baseline()` already writes under `evaluation/suites/*_baseline.json`.
  It reads what evaluation runs left on disk rather than re-running suites itself, so it is live
  over turns and current-as-of-last-run over evaluation. Verified against a real Postgres instance,
  which caught a genuine production-blocking bug the in-memory sink could never surface: `avg()`
  over an integer column returns `Decimal` via asyncpg, and `json.dumps` cannot serialise a
  `Decimal` — every real deployment's `/admin/scorecard` call would have 500'd. Fixed by casting to
  `float` before rounding; the same class of bug as Phase E1's `DomainEvent.as_dict()` finding,
  caught for the same reason: this session's standing rule to verify against real Postgres, not
  just the in-memory adapter every other suite runs on.
- **G2. Done.** `tests/unit/test_llm_routing.py` already tests `ProviderRouter`'s scoring,
  capability filtering, failover, and circuit-breaker logic thoroughly — but only against
  synthetic/scripted providers. The specific, narrower gap this roadmap named was two
  *real* backends running at once; every prior real run (including Phase A4's load test) used
  exactly one real provider (Ollama) plus the echo provider. Reused Phase A4's cached
  `ollama/ollama` image and model volume to stand up one Ollama server with two real,
  differently-sized models (`smollm2:135m`, 135M params; `qwen2.5:3b-instruct-q4_K_M`, 3B params)
  loaded simultaneously, then built two real `OllamaProvider` instances against it — no mocks, no
  injected timing. Result, measured: `FASTEST` correctly ordered the smaller model first from real
  observed latency (~130ms vs ~480ms per completion); `QUALITY_FIRST` and `BALANCED` correctly
  ordered the larger, higher-`quality_tier` model first; failover from an unreachable provider
  (connection refused, not a bad request) correctly reached the second real provider and returned
  real generated text. `CHEAPEST` does not distinguish the two — both are local models with a real
  cost of `$0`, so the router's own name-order tie-break decides, which is the honest behaviour of
  a "cheapest" policy when nothing actually costs anything, not a bug. Written up as a permanent,
  opt-in test, `tests/load/test_multi_model_routing.py`, following `test_real_data_tier.py`'s exact
  pattern: skipped cleanly without `AURA_ROUTING_TEST_*` env vars, excluded from the default
  `pytest tests` run by the same `load` marker. Ran clean (4/4) against the real two-model server
  before it was torn down.

**Exit criteria.** Scorecard fed by real telemetry and real eval baselines, not hand-maintained
numbers — **done**, and verified against real Postgres closely enough to catch a real
serialisation bug. Multi-model routing proven against two real, simultaneously-running backends,
not Ollama-plus-echo — **done**, with a permanent opt-in test reproducing the proof.

## 4. Scope decisions

Recorded so they are not re-litigated.

| Deferred | Reason |
| --- | --- |
| Leads / CRM / lead scoring | No business currently uses AURA. A scoring model with no data is a rules table with extra steps. |
| Revenue analytics and forecasting | Same. `FinanceRecord` tracks personal income and expense; forecasting personal cashflow is a Phase D+ question, not a Business OS. |
| Social publishing (Instagram, LinkedIn) | Outbound posting is the highest-risk tool class in the proposal and the least connected to AURA's current use. It would need Phase C's permission model proven first. |
| Kubernetes / microservices | The VPS stack has not been deployed once. Scaling decisions come after Phase A produces a real concurrency ceiling. |
| Training or fine-tuning a base model | Out of scope by ADR-008; the pipeline prepares datasets and requires human approval to promote. |

## 5. Scorecard

Seeded honestly. "Unmeasured" is the correct current value for most rows and should be replaced by
Phase A, not by an estimate.

| Metric | Current | Source once measured |
| --- | --- | --- |
| Recall@5, nDCG, MRR (hybrid, one corpus) | **recall 0.65, nDCG 0.51, MRR 0.37** on the 10-doc `docs_corpus` suite ([A3 report](reports/phase-a3-retrieval-corpus.md)) — measured, not tuned; per-strategy breakdown still open | Phase A3 suites |
| Groundedness / citation precision | Unmeasured — needs `AnswerEvaluator` gated on a real/scripted provider | Phase A3 follow-up |
| Single-turn latency, real data tier | **p50 56.3 s / p95 70.5 s**, full free-text answer, 3 CPU cores, `qwen2.5:3b-instruct-q4_K_M` ([A4 report](reports/phase-a4-load-test.md)) — not comparable to A1's 2-output-token warm/cold figures | Phase A4, measured |
| Concurrency ceiling | **Flat throughput (~0.04–0.06 turns/s) from 1 to 8 concurrent requests** — `OLLAMA_NUM_PARALLEL=1` is the binding constraint, confirmed rather than assumed ([A4 report](reports/phase-a4-load-test.md)); 478 turns/s remains an echo-provider artifact, not this number | Phase A4, measured |
| Gateway image size | **2.34 GB**, down from 8.88 GB once CUDA was dropped ([A1 report](reports/phase-a1-first-boot.md)) | Phase A1 finding 2 |
| Tool success rate | **Live** via `GET /api/v3/admin/scorecard` (`TelemetrySink.recent_summary`); no real production traffic yet to report a baseline number from | Phase G1, mechanism done |
| Task completion rate | Unmeasured | Phase C |
| Scanned-document OCR | **Real**, Tesseract, proven on a genuinely scanned PDF end to end through `POST /knowledge/documents/upload`; no vision model (image/chart understanding) | Phase F1 |
| Voice (STT/TTS) | **Real**, self-hosted (`faster-whisper` / `espeak-ng`), proven with a real synthesize→transcribe round trip through `POST /voice/speak` and `/voice/transcribe` | Phase F2 |
| Transcript NLP | **Scaffolding** — `aura_core/nlp/` normalize + shallow intent; quality on real ASR unproven | Phase I |
| Multi-model routing | **Proven** against two real, simultaneously-running local models (135M and 3B params) — `FASTEST`/`QUALITY_FIRST`/`BALANCED`/failover all route on real measured behaviour; `CHEAPEST` ties (both cost `$0`) | Phase G2, `tests/load/test_multi_model_routing.py` |
| Restore verified | **Yes for all three stores** — Postgres, Qdrant and Redis each restored from the `backup` service's own output ([A2 report](reports/phase-a2-restore-drill.md)); offsite copy and `restore.sh` still unexercised | Phase A2 |
| Production deployments | **1 and counting** — the real VPS, 17 services, verified live over HTTPS on 2026-08-23 | Phase A1 |

---

Related: [Production Readiness Score](../AURA_AI_PRODUCTION_READINESS_SCORE.md) ·
[Current Architecture](../AURA_AI_CURRENT_ARCHITECTURE.md) ·
[Architecture Overview](architecture/00-overview.md) ·
[Missing Components](analysis/03-missing-components-report.md) ·
[V2 Blueprint](../Doc/V2_BLUEPRINT.md)
