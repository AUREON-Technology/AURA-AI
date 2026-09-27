# AURA Upgrade Roadmap

> **Superseded as the forward plan (2026-09-25).** The canonical roadmap is now
> [`docs/roadmaps/AURA_MASTER_UPGRADE_ROADMAP.md`](roadmaps/AURA_MASTER_UPGRADE_ROADMAP.md); every phase in this file is mapped there by
> [`AURA_ROADMAP_RECONCILIATION.md`](roadmaps/AURA_ROADMAP_RECONCILIATION.md).
> This file is kept as a historical evidence ledger; do not add new phases here.

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
| A3 | Build a labelled retrieval/answer corpus and gate CI on it. **Retrieval and answer-grounding both done.** A 10-document corpus (the repo's own `docs/architecture/*.md`) with 23 graded queries now gates every PR through `pytest tests` — no workflow edit needed, `testpaths` already discovers `tests/e2e/test_docs_corpus_gate.py`. The retrieval baseline is real and unflattering (recall@5 = 0.65, pass_rate = 0.61) because the default `HashEmbeddingProvider` is non-semantic; published as measured, not tuned to pass. See [phase-a3-retrieval-corpus.md](reports/phase-a3-retrieval-corpus.md). Answer/citation/groundedness gating — the half this row used to name as open — is now closed too: `AnswerEvaluator` needed "a real or scripted provider" because the default `EchoProvider` echoes the user's own message, sharing no vocabulary with retrieved content, so a groundedness score was either undefined or an artifact of incidental overlap. `ContextGroundedProvider` (new, `aura_core/llm/providers/echo.py`) answers with excerpts of the retrieved `<<<AURA-DATA:...>>>`-fenced content instead of the query — still scripted, not a real model, but one that actually reads what it was shown. Building it surfaced two real bugs, both fixed and regression-tested before this was trusted: a fence-parsing regex (`[^>]*>>>`) that stopped at the first literal `>` inside a `source="..."` attribute, which can itself contain one (a heading path like "kernel > Context"); and excerpts that quoted whole sentences verbatim tripped `SystemPromptLeakGuard` (the fenced knowledge is itself part of the system prompt, so verbatim reuse is indistinguishable from a leaked-prompt attack) — fixed by capping every excerpt at 6 words with an explicit break between *every* chunk, sentence boundaries included, since two sentences pulled from one block are themselves adjacent in the source and a plain space between them silently reconstructed a long real run. Baseline (`docs_corpus_answer_baseline.json`): **groundedness 0.94, citation coverage 0.83** across the 23-case corpus. 6 new provider unit tests (`tests/unit/test_context_grounded_provider.py`), each confirmed to fail against the pre-fix code. Still open: per-RAG-strategy comparison across `aura_core/rag/{adaptive,agentic,base,expansion,leaves}/`. | `EvalSuite.from_jsonl` (`aura_core/evaluation/harness.py:77`), `RetrievalEvaluator`, `AnswerEvaluator`, `RegressionGate` — no new metrics were written, only a provider that makes the existing ones measurable. |
| A4 | Load test with the real data tier. **Done.** `aura_backend/tests/load/test_real_data_tier.py` boots the real `Container` — Postgres, Qdrant, Redis, Ollama — and measures single-turn latency and a concurrency ceiling with `qwen2.5:3b-instruct-q4_K_M` in the path. Its first run found a real, previously-undetected defect: `IndexIdentity.matches_embedding` compared a raw embedder id against one recovered through a lossy slug round-trip, so retrieval silently returned nothing against production's real `sentence-transformers` embedder on every query — invisible to every prior test because they all use `HashEmbeddingProvider`, whose id happens to already be its own slug. Fixed (`aura_core/domain/knowledge.py`) and regression-tested before the numbers below were captured. p95 latency **70.5 s** (CPU-only, 3-core sandbox — not comparable to a GPU deployment). Concurrency throughput is **flat from 1 to 8 concurrent requests** (`OLLAMA_NUM_PARALLEL=1` serializes every generation), confirming the concurrency ceiling the compose file's own comments already asserted. See [phase-a4-load-test.md](reports/phase-a4-load-test.md). | Extend the harness in `tests/benchmarks/bench_pipeline.py` — its conditions, not its numbers. |
| A5 | `pip-audit` in CI. **Already done before this roadmap existed** — `.github/workflows/backend-ci.yml`'s `pip-audit` job (AURA-020); see §1's baseline-correction table. Listed here only so the phase table is complete. | — |

**Exit criteria.** A real recall@10 / nDCG / groundedness figure per RAG strategy, on a real
corpus — **partial**: recall/nDCG/MRR and groundedness/citation coverage are both measured and
gated now (A3), for the default hybrid strategy only — per-strategy breakdown across
`aura_core/rag/{adaptive,agentic,base,expansion,leaves}/` remains open. A real p95 with a model in
the path —
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
- **C3. Fixed, not built new — the "Build Agent Evaluation System" commit (`41c3a95`) shipped a
  task-completion evaluator that had never once completed a run.** `AgentEvaluator.run()`
  (`aura_core/evaluation/harness.py`) referenced `TurnRequest` without importing it, so every
  invocation raised `NameError` on the first turn; `evaluation/runner.py`, its only caller,
  independently could not have reached that far — it built settings via bare `AuraSettings()`
  instead of `load_settings()` (zero LLM providers configured, `ConfigurationError`), resolved
  `BACKEND_ROOT` one directory too high (`.parents[2]` instead of `.parents[1]`, so the suite file
  was never found), and copied a case's fields via `case.__dict__` on a frozen `slots=True`
  dataclass, which does not have one. Stacked, these four bugs meant `eval-ci.yml` (manual
  `workflow_dispatch`, already not merge-blocking) could never have succeeded even once. Separately,
  the 50-case dataset (`evaluation/suites/agent_tasks.jsonl`) keys every case on
  `expected_substrings`, a field `AgentCase`/`load_agent_cases` never read — the loader silently
  dropped it, so every case fell through to a generic "does the reply contain a marker word like
  'done'" check that nothing in the dataset was written to satisfy. All five fixed; `AgentCase`
  gained the `expected_substrings` field, `_is_task_complete` checks it against the accumulated
  turn text (falling back to `success_criteria`, then the marker heuristic, in that order), and 11
  new tests in `tests/unit/test_evaluation.py` exercise the real kernel round trip against the
  `container` fixture — each one verified to fail against the pre-fix code before being counted as
  a regression test. First-ever real, measured result against the default `EchoProvider`: 84%
  pass rate, 8 of 50 cases failing on genuine dataset wording mismatches — `expected_substrings`
  asking for `"17"` where the query says "5pm", or `"memory"` where the query says "memories" —
  not harness bugs, named as follow-up rather than fixed in that pass. That follow-up is now
  done too: each mismatched entry was corrected to the query's own literal wording (the smoke
  gate can only check what a verbatim echo actually contains), landing a genuine **100% pass
  rate, 100% tool success rate, 100% task completion, 50/50 cases** — not inflated, since every
  expectation is now something the query text actually contains.
  Wired as an actual regression gate for the first time via `tests/e2e/test_agent_tasks_gate.py` +
  `evaluation/suites/agent_tasks_baseline.json`, mirroring `test_docs_corpus_gate.py`'s existing
  pattern (Phase A3) so it runs inside the ordinary `pytest` job on every PR — no new GitHub Actions
  workflow needed, since `backend-ci.yml` already runs `pytest tests` on every `pull_request`.
- **C4. Done — the update/delete half of C2's own named follow-up.** `aura_core/tools/records.py`
  had `create_*`/`list_*` only; `update_*`/`delete_*` were named as "the same shape of change as
  create," not silently dropped, but never actually built. The storage layer was already there and
  untouched by this change — `PersonalOsStore.get`/`update`/`delete` (`aura_core/ports/storage.py`)
  and both adapters (`InMemoryPersonalOsStore`, `PostgresPersonalOsStore`) already implemented the
  full port, owner-scoped and correct, so this is a tool-layer-only addition. `update_{noun}` takes
  a `record_id` plus every `create_{noun}` field made optional (`_as_optional`, a
  `dataclasses.replace` over each `ParameterSpec`) and merges only the supplied ones via
  `PersonalOsRecord.with_fields`; `delete_{noun}` removes one outright. Both re-check the fetched
  record's `kind` before acting — found while implementing, not in the original ask:
  `PersonalOsStore.get`/`delete` are scoped by id and owner only, not kind, so without that check
  `update_task` given a calendar event's id would have silently merged task-shaped fields into it,
  and `delete_task` could have deleted a record of a different kind outright. Regression-tested by
  temporarily removing the check and confirming the new cross-kind tests fail without it. Granted
  to the three personas that already hold the matching `create_*`/`list_*` pair (`task_planner`:
  `update_task`/`delete_task`/`update_calendar_event`/`delete_calendar_event`; `finance_advisor`:
  `update_finance_record`/`delete_finance_record`; `health_companion`:
  `update_health_record`/`delete_health_record`) in `aura_core/agents/registry.py`. 12 new tests in
  `tests/unit/test_personal_os_records.py` (store-level `update`, plus tool-level partial-merge,
  no-op, not-found, cross-kind, owner-scoping, idempotent-delete, and out-of-grant-denial cases),
  full `pytest`/`tests/architecture`/the legacy suite all green. Not addressed here, and not
  claimed: syncing these records to the Flutter client (still server-visible only, per C2's own
  note) and optimistic-concurrency/versioning on update (last write wins, same as `put`).
- **C5. Fixed a docstring/implementation gap in `AgentEvaluator`, found while re-reading it.**
  Its class docstring claims it measures "task completion, tool selection accuracy, tool success
  rate, recovery from failures, and adherence to expected tool sequences" — but `AgentCase.
  expected_tools` was loaded by `load_agent_cases` and never read anywhere in `run()`. A case
  naming which tools mattered got no credit or penalty for using the wrong ones, or none at all.
  Added `AgentCaseResult.tool_accuracy` (`AgentEvaluator._tool_accuracy`, new): a recall-style
  measure over the *set* of tools used across the run, `1.0` when a case names no `expected_tools`
  (nothing was specified to get wrong, the same vacuous-pass convention `tool_success_rate` already
  uses). Exposed in `AgentSuiteResult.metrics()` as `tool_accuracy`. Deliberately does not change
  `success`'s own pass/fail logic — a behavioural change to the existing gate's semantics is a
  separate decision, not implied by fixing a measurement gap — so this is inert against the
  current `agent_tasks.jsonl` baseline (no case in it sets `expected_tools` yet) and only matters
  once a case does. 5 new tests in `tests/unit/test_evaluation.py`, including one that runs a real
  case through `AgentEvaluator.run()` and confirms it scores `0.0` rather than the `1.0` a
  vacuous-pass bug would silently produce; confirmed all three non-trivial tests fail when the
  computation is stubbed back to always-`1.0`.

**Exit criteria.** An agent's tool set is data, not code — **done** (C1). A tool call outside an
agent's grant fails closed and is visible in the audit sink — **done**, proven against both a
synthetic tool (C1) and a real C2 tool (`create_health_record` denied for `finance_advisor`).
Task-completion rate is measurable through the Phase A harness — **done** (C3): a task-completion
evaluator exists, actually runs, and is gated in CI, with the caveat that the current 100%
baseline measures the `EchoProvider` round trip, not real-model task quality. Full CRUD
against the server-owned personal-OS table — **done** (C4): create, read (via list), update, and
delete all exist, are owner- and kind-scoped, and are granted to the personas that need them.

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
  constructing the adapter never makes a network call. Primary TTS is Piper
  (`adapters/piper_tts.py`, `en_US-lessac-medium` / `ar_JO-kareem-medium` with prosody tuning via
  `VoiceSettings`); `EspeakTtsEngine` (`adapters/espeak_tts.py`) remains the last-resort fallback
  when a language's ONNX is missing — chosen because it needs no model weights and no extra pip
  dependencies. Both keep inference on our own infrastructure; no hosted vendor branch exists
  anywhere in `aura_core/llm/registry.py`, and none was added here either.
  Wired through `VoiceSettings` and two endpoints, `POST /api/v3/voice/transcribe` and
  `POST /api/v3/voice/speak`, both returning 503 rather than silently degrading when the engine
  is unavailable. Proven with a real, no-mocks round trip: synthesis of "one hundred dollars" to
  WAV, `faster-whisper` transcribes it back, at three levels — direct adapter calls, the wired
  `Container`, and full HTTP through `TestClient`.
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

### Phase H — Live voice chat

**Status: scaffolding landed; not proven.** Design: [`docs/plans/phase-h-live-voice-chat.md`](plans/phase-h-live-voice-chat.md).
Analysis: [`docs/reports/voice-nlp-analysis.md`](reports/voice-nlp-analysis.md).

Full-duplex WebSocket session (`/api/v3/voice/live`) that endpoints mic PCM with energy VAD,
wraps segments as WAV for existing batch STT, runs `TurnRequest(route="voice_live")` through
`AIKernel.stream()`, synthesizes per sentence (held until `done` when confirmation/guards apply),
and streams PCM back. Flutter full-screen `/voice` entry (mobile only) plus protocol/socket/UI
scaffolding. In-memory speech adapters close the house-rule gap for default pytest.

**Not claimed done.** Exit gates still open: real Piper→Whisper load round-trip
(`tests/load/test_live_voice_roundtrip.py`), on-device AEC/barge-in feel (Android **and** iOS),
first-PCM-after-`speech_ended` latency under a named model, Arabic live STT on multilingual
Whisper, Redis-backed exact multi-worker session caps. Client CI is the Dart gate — no Flutter
toolchain in this environment; audio is not exercised there.

A production edge-layer defect was found and fixed since (PR #65, 2026-09-07): `/api/v3/voice/live`
404'd for every real client because `http2 on;` on the nginx HTTPS server block negotiated HTTP/2
via ALPN for any client offering it (curl, and Dart's `dart:io` WebSocket client), and HTTP/2
forbids the legacy `Connection`/`Upgrade` headers the WebSocket handshake needs — confirmed via
`docker exec`/`curl -v` showing the identical request succeed over HTTP/1.1 straight to the
gateway and 404 through nginx. Nothing in the app, the route registration, or the location block
itself was wrong. This was purely an edge-config defect, so it doesn't move Phase H's own exit
gates above, but it was the reason the whole feature was unreachable from any real client before
this fix, regardless of what those gates otherwise measure.

### Phase I — Transcript / text NLP

**Status: scaffolding / partial.** Design: [`docs/plans/phase-i-nlp.md`](plans/phase-i-nlp.md).

Stdlib-first `aura_core/nlp/` (`NlpEngine`) normalizes chat messages and STT transcripts before
`TurnRequest` construction; optional shallow intent + `RuleEntityExtractor` metadata (not written
to the graph). Wired through `container.nlp` and chat `to_turn`; live voice calls the same
`normalize_for_turn` extension point. **Not** a spaCy stack; no hosted NLP. Proven quality gains
on real ASR transcripts are still required before marking the phase done.

### Phase J — Persistent engineer-agent workspaces

**Status: built, not proven.** Design: [ADR-013](architecture/013-agent-workspaces.md),
[ADR-014](architecture/014-workspace-local-side-effects.md). Sibling of the **Desktop Engineer**
Phase J below (ADR-011 / `aura_hostd`): this track is the *server-side Docker* persistence path.
Early plan text that numbered these decisions as ADR-010/011 is stale — those numbers are CAG-lite
and the desktop Engineer agent.

Units 1–11 shipped on `main` via [PR #70](https://github.com/anasibrahimgnedy-collab/AURA-AI/pull/70)
(`91b6d5c`): durable per-(user, repo) workspaces, host-side git, confirmation at the workspace
boundary, v3 API + client callers, reaper, hardened image/egress. Client and Backend CI were
green; Deploy VPS succeeded, so the VPS checkout includes that code. [PR #72](https://github.com/anasibrahimgnedy-collab/AURA-AI/pull/72)
(`97ed3a9`) mounts `EngineerWorkspacesPanel` on the Engineer history page and aligns
`docker-compose.dev.yml`'s sandbox profile with prod. Sandbox remains **opt-in**: production
still runs with `AURA_SANDBOX_ENABLED=false` and without `COMPOSE_PROFILES=sandbox`, so none of
the live checks below have been observed on the VPS.

The engineer agent gets a durable Docker workspace per (user, repository): a real `git clone`
with history, surviving between turns, deleted after 72 hours without activity. Git runs
host-side so the token never enters a container executing model-chosen commands. Capacity is
bounded by *running* containers rather than stored workspaces, since a workspace is stopped
between turns and a stopped container holds no memory. `sandbox_egress` became `internal: true`
behind an allowlisting proxy, which closes the open follow-up the compose file flagged in its own
comment. Confirmation now gates the workspace boundary rather than every write inside it
(ADR-014), which is what keeps the prompt at `git_push` meaningful.

Backend and client tests pass (`pytest`, the legacy `unittest` tree, the architecture fitness
tests, and a real-PostgreSQL check of the new adapter). **That is not evidence the feature
works on the VPS.** Code present with the sandbox profile down is still unproven. What is
outstanding, on the VPS with `AURA_SANDBOX_ENABLED=true` and the `sandbox` profile up.
Checks 1-9 are scripted — `bash deploy/scripts/verify-engineer-workspaces.sh` runs them
against the real daemon through the gateway's own wiring and writes a report to
`docs/reports/`. Cite that report here rather than this list when marking the phase
proven; check 10 needs a real agent session and stays manual.


0. `docker exec` is still refused by the socket proxy (403). Not in the original list, and
   the reason commands run as their own container: if `EXEC` were ever turned back on, the
   adapter would keep working and nothing would report that the allowlist had widened.
1. `write_file` then `run_command cat` round-trips — proves the bind-mount path fix.
2. `clone_repository`, then `git log` shows history.
3. `grep -rn ghp_ /workspace/.git` finds nothing — no credential in the checkout.
4. `id` reports uid 10001; writing outside `/workspace` fails.
5. Egress allowlist holds: PyPI succeeds, an unlisted host does not.
6. A second turn on the same repository reuses the workspace with files intact.
7. `docker ps` between turns shows it stopped, and `free -m` unchanged.
8. `docker compose restart gateway` re-adopts it by label.
9. A backdated `last_active_at` plus the beat task removes container, directory and row.
10. A full clone → edit → test → push session produces exactly **one** confirmation prompt.

Per this document's own standing rule, this phase is not "done" until those have been observed.

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

- **G3. Fixed a real race `CLAUDE.md` claimed was already handled.** `CLAUDE.md` names
  `PostgresPool._get_pool()` as "the canonical example" of lazy adapter initialization "behind an
  `asyncio.Lock` when concurrent callers could race" — but the method had no lock at all. N
  concurrent first callers each saw `self._pool is None` before any of them finished awaiting
  `asyncpg.create_pool`, so each built its own pool; only the last write to `self._pool` survived,
  and every other pool's connections leaked, never closed by anything. Found by reading the method
  against the claim, not by a failure report — the in-memory-only default `pytest` suite has no way
  to exercise real concurrent Postgres pool creation, so nothing before this pinned the gap between
  the doc and the code. Fixed with double-checked locking: a new `self._pool_lock`
  (`asyncio.Lock`), acquired only around pool *creation*, with a second `self._pool is None` check
  once inside it (a caller that waited for the lock may find another caller already built the pool
  while it queued). Schema readiness is deliberately left outside the lock, unchanged — its own
  docstring already documents why: every statement is `IF NOT EXISTS`, so concurrent callers racing
  it converge rather than one failing. No real Postgres exists in this environment, so
  `tests/unit/test_postgres_pool_concurrency.py` (new, 6 cases) monkeypatches `asyncpg.create_pool`
  with a fake that actually awaits (so a race has room to happen) and returns a distinct object per
  call (so "did every caller get the *same* pool" is a real assertion, not a tautology): cold
  start, 20 concurrent first callers, repeated calls after warm-up, failure during initialization,
  recovery after failure, and concurrent callers all queued behind a failing attempt. Confirmed the
  core test (20 concurrent callers) fails against the pre-fix code — 20 separate `create_pool`
  calls instead of 1 — before counting it as a regression test.
- **G4. Closed a DNS-rebinding gap in `fetch_url`'s SSRF guard.** `_assert_public_url`
  (`aura_core/tools/builtin.py`) rejects an IP-literal URL directly and checks a hostname against
  the allowlist, but never checked what an allowlisted *hostname* actually resolves to — its own
  comment already named the intended defence ("the private-range check is defence in depth for an
  allowlisted name that resolves somewhere unexpected") without the code doing it. An allowlisted
  domain whose DNS record points at `169.254.169.254` (cloud instance metadata) or `127.0.0.1`
  would have been fetched without complaint, string allowlist satisfied. Added
  `_assert_resolves_publicly`: resolves the hostname (`asyncio.to_thread(socket.getaddrinfo, ...)`,
  off the event loop since DNS lookups block) and rejects if *any* returned address is
  private/loopback/link-local/reserved, wired into `fetch_url` right before the request. Narrows,
  rather than eliminates, the gap — a genuine TOCTOU window remains between this check and the
  connection `httpx` makes a line later, disclosed in the function's own docstring rather than
  claimed as closed; full IP-pinning would close it and is named as follow-up, not done here. 6 new
  tests in `tests/unit/test_builtin_tools.py` (`TestDnsRebindingGuard`): public resolution passes,
  rebinding to cloud metadata / loopback is refused, one private address among several A-records is
  enough to refuse, unresolvable hosts are refused rather than silently allowed through, plus one
  integration test calling the real `fetch_url` tool end-to-end (not just the guard function) to
  prove the check is actually wired in, not just present and uncalled — confirmed to fail with the
  wiring call removed before counting it as a regression test.

### Phase J — Desktop Engineer agent

**Started, 2026-09-08. Backend bridge, daemon core, output streaming, pairing auth (daemon and
app), the app's own desktop login, a first workspace-management UI (daemon supervision,
pairing, root grants, file browsing), and Linux/Windows desktop build scripts with CI are done;
a real PTY, interactive consent tiers, live terminal streaming to a client, diff review outside
of chat, and a bundled `aura_hostd` binary are not.**

This phase did not exist in any prior version of this roadmap — a gap worth naming plainly, since
the standing rule elsewhere in this document is that undocumented-but-built capability gets
corrected here. The Engineer persona and its tools (`aura_core/tools/coding.py`) predate this
phase entirely and were never described here either; see [ADR-011](architecture/011-desktop-engineer-agent.md)
for the desktop/`aura_hostd` design. Server-side persistent Docker workspaces are a separate
phase and decision pair ([ADR-013](architecture/013-agent-workspaces.md) /
[ADR-014](architecture/014-workspace-local-side-effects.md)), not this one.

- **J1. Done.** `aura_core/ports/coding.py::SandboxRunner.create_task` now takes an optional
  `user_id`, threaded from `_SandboxSessionCache` (`tools/coding.py`), so a runner that must route
  per-caller has the identity it needs without every other runner knowing why.
- **J2. Done.** `adapters/host_agent.py` (`HostAgentSession`, `HostAgentRegistry`,
  `HostAgentSandboxRunner`, `CompositeSandboxRunner`) and `api/host_agent.py`
  (`/api/v3/host/agent`, a duplex-RPC WebSocket shaped like `voice_live.py`) let the engineer
  agent's sandbox tools route to a caller's connected desktop daemon instead of only the
  server-side Docker sandbox. `container.sandbox_runner` is now always a
  `CompositeSandboxRunner`; a paired desktop app serves the engineer tools even where
  `sandbox.enabled` was never turned on. 19 new tests
  (`tests/unit/test_host_agent.py`), all real (in-process fake sessions, not mocks) —
  `pytest`/`tests/architecture`/the legacy `unittest` suite all stay green, no Docker required.
- **J3. Core done; PTY and interactive consent not started.** The `aura_hostd` daemon (new
  top-level `aura_hostd/`, a pure-Dart package with no Flutter dependency) now implements: an
  outbound WebSocket client speaking `HostAgentSession`'s exact frame protocol, workspace-root
  grants persisted locally (`aura_hostd grant/revoke/trust/untrust/roots`), path containment
  checked *after* resolving symlinks (`lib/src/containment.dart`, matching
  `tools/builtin.py::resolve_within`'s existing discipline) with a hard denylist on what can ever
  be granted as a root, and the `write_file`/`read_file`/`run_command` RPC handlers. Command
  policy (`lib/src/command_policy.dart`) is a disclosed simplification rather than the designed
  tiered UX — there is no UI yet to prompt a human per call, so an untrusted workspace only runs a
  small read-only allowlist and a trusted one allows anything not on the hard denylist; three
  review passes against this allowlist found and closed real bypasses before merge (`find
  -delete`/`find -exec` mutating without needing any blocked shell metacharacter; `env <command>`
  laundering an arbitrary command through what looks like an inspection tool; and chained/
  redirected/substituted commands slipping past a naive "starts with an allowlisted verb" prefix
  match — closed by blocking shell metacharacters structurally before the allowlist is even
  checked, not by chasing individual payloads). Windows command execution is still a gap (the
  backend always sends `["sh", "-c", ...]`; a Windows machine with no POSIX shell on `PATH` can't
  run commands via this path yet — filesystem ops and grants are unaffected). `dart test` covers
  containment (including symlink-escape and `..`-traversal), the command-policy allowlist
  (including the specific bypasses above), and the wire protocol end-to-end against a local
  in-process server; `.github/workflows/hostd-ci.yml` is the actual verification, since this
  environment has no Dart toolchain (confirmed: `dart`/`flutter` both absent) to run `dart
  test`/`dart analyze` itself.
- **J3b (Unit 4). Streaming done; a real PTY is not.** `run_command`'s output now streams end to
  end instead of only arriving as one blob after the command exits: `ports/coding.py` gained
  `SandboxOutputEvent`/`StreamingSandboxRunner` (optional, `isinstance`-detected, purely additive
  over `SandboxRunner.run`); `HostAgentSession.call_streaming` yields `stream` frames as they
  arrive then the terminal `result`, with an *idle* timeout between frames rather than one fixed
  deadline; `aura_hostd`'s `run` RPC handler takes an `onChunk` callback the WebSocket client wires
  to send `stream` frames per output chunk; both sides cap accumulated output independently
  (`_maxOutputCapChars=500_000` daemon-side, `_RUN_COMMAND_STREAM_CAP_CHARS=200_000`
  backend-side) while still draining the process so a full pipe can't reintroduce the orphan-hang
  bug J3's timeout-kill path already fixed once. Unlike J3, this environment does have a working
  `pytest`, so the entire backend half was actually run, not asserted: 13 new tests
  (`test_host_agent.py`, `test_engineer_tools.py`), a fitness-test update extending the
  "every port method is `async def`" rule for `run_streaming`'s deliberate exception (matching
  `LLMProvider.stream`'s existing one), `pytest`/`tests/architecture`/the legacy `unittest` suite
  all green, no regressions. What "PTY" actually means here is disclosed, not implied: no raw
  terminal mode, no resize, no job control — `_lineBuffered` wraps a command with `stdbuf -oL -eL`
  when that GNU-coreutils binary is on `PATH` (present on Linux, generally absent on stock macOS,
  skipped on Windows), which forces real incremental output for common C-based CLI tools but does
  nothing for an interpreted runtime that decides its own stdout buffering (Python's io layer,
  notably). And critically: **nothing consumes this yet.** The data provably flows daemon ->
  backend (verified over a real WebSocket, `dart test`'s `rpc_client_test.dart`, and in
  `pytest`), but no SSE `step` frame or client renders it — `run_command`'s tool-facing return
  value is still one string, unchanged. Wiring per-chunk progress through the kernel's
  `StepEvent`/SSE machinery is deferred to when Unit 8 actually has a client to show it to.
- **J3c. Done.** `write_file`/`run_command`/`clone_repository`'s tool descriptions no longer
  unconditionally claim "not the user's real files" / "cannot reach the user's real network" --
  true only for the Docker-fallback path and false once `CompositeSandboxRunner` routes a task to
  a paired desktop app instead, decided per task rather than per deployment. Descriptions now say
  both possibilities rather than assert one; the fallback's real network restriction is kept, not
  dropped. Locked in by
  `test_engineer_tools.py::TestSandboxToolDescriptionsAreHonestAboutRouting`.
- **J4. Done.** Desktop pairing auth — not by extending `services/security.py`'s attestation path
  to recognise a `desktop` platform (that path stays mobile/web-only, untouched), but a separate,
  parallel identity path: `services/device_pairing.py` (new) implements the RFC-8628-shaped
  device authorization grant this ADR describes — `start_pairing()` returns a short human-readable
  `user_code` plus an opaque `pairing_id`; `approve_pairing()` requires the approving caller
  already hold a valid access token (an existing authenticated session standing in for the
  attestation a desktop OS has no equivalent for) and binds the pairing to that identity;
  `poll_pairing()` is consumed exactly once on the first successful poll after approval.
  `services/jwt_auth.py::issue_device_token` mints a 30-day, no-refresh-token access token whose
  only revocation mechanism is a `device_id` + owner check on every `verify_access_token` call
  (`revoke_device`, ownership-checked so a caller can't revoke someone else's paired device, and
  indistinguishable in its failure response from "unknown device" so it can't be used to probe for
  one). Mounted as `POST /api/v3/auth/desktop/pair/{start,approve,poll}` and
  `/desktop/devices/revoke` on `api/v2/auth.py`'s existing router. `aura_hostd auth pair
  [--backend-url <url>]` (`lib/src/pairing_client.dart`, new) is the daemon-side half: start ->
  show the code -> poll at the server-advised interval -> save the resulting token the same way
  `auth set-token` would have. This environment has a working `pytest`, so the backend half was
  actually run: 19 new tests (`test_device_pairing.py`, legacy `unittest` tree, covering the
  service layer, token issuance/verification/revocation, and the full HTTP round trip through a
  real FastAPI app), full `pytest`/`tests/architecture`/the legacy suite all green, no
  regressions. The daemon half (`pairing_client_test.dart`, four cases against a local fake HTTP
  server) is written but unverified locally — no Dart toolchain here, `hostd-ci.yml` is the real
  check, per this repo's own conventions. **Still missing:** a UI for the `approve` step — today
  it is only reachable via a raw authenticated API call, since J5's client does not exist yet.
- **J5a (Unit 7). Login done; the workspace UI is not.** `google_sign_in` has no Linux/Windows
  implementation (confirmed absent from both platforms' `generated_plugins.cmake`), so the
  desktop app's own login went through a real RFC 8252 loopback-redirect OAuth 2.0 + PKCE
  exchange against Google instead (`aura_client/lib/core/auth/desktop_oauth_client_io.dart`,
  behind the same `dart.library.ffi` conditional-export pattern
  `core/database/connection/open_connection.dart` already uses, so the web build -- which keeps
  `google_sign_in`, unaffected -- never pulls in `dart:io`). This is a *separate* identity path
  from J4's `aura_hostd` pairing flow: a headless daemon has no browser to run OAuth in, but
  `aura_client` on desktop does. It needs a second, real Google OAuth client (Google restricts
  the loopback-IP redirect URI this flow requires to "Desktop app"-type clients, and the existing
  `google_client_id` is a "Web application"-type client used for mobile/web) --
  `aura_backend/config.py` gained `google_desktop_client_id` (env
  `AURA_GOOGLE_DESKTOP_CLIENT_ID`). A real "Desktop app"-type client was registered 2026-09-10
  (Google Cloud project `aura-ai-anas-gnedy`) and is now hardcoded as its default, the same way
  the mobile/web `google_client_id` above it always has been -- an OAuth client id is not a
  secret, so this needs no deploy-time configuration; `aura_client/lib/core/config/config.dart`'s
  `googleDesktopClientId` carries the matching id as its own hardcoded default. **Correction from
  a real first run, not review:** Google's token endpoint rejected a PKCE-only exchange against
  this client with `invalid_request: client_secret is missing`, contradicting the "public client,
  no secret needed" assumption ADR-011 made at design time -- `config.dart` gained a matching
  `googleDesktopClientSecret`, deliberately never hardcoded or committed unlike the client id
  (only injected at build time via an untracked `deploy/.env` entry and `--dart-define`,
  resolved by `scripts/deploy_client_desktop.sh`/`.ps1`) and the token exchange now sends it
  alongside PKCE. The backend never sees this secret; it only checks the client id as an
  audience. `services/security.py::verify_identity_token` now accepts either
  client's audience rather than a single hardcoded one. The resulting real-but-unattested Google
  identity is exchanged via the new `POST /api/v3/auth/desktop/login` for the same kind of
  narrower, revocable device-scoped token J4's pairing issues (not a full session pair, and not
  gated to console operators the way `/web/token` is -- every AURA user should be able to log
  into the desktop app). Backend: 8 new tests (`test_desktop_login.py`), full
  `pytest`/`tests/architecture`/the legacy suite (100 tests) all green -- also fixed a
  pre-existing flaky assertion in J4's own `test_device_pairing.py` (its user-code regex excluded
  a character the alphabet actually includes, confirmed fixed over 30 runs). Client: `TokenManager`
  gained `exchangeDesktopLogin`/`applyPersistedDesktopToken` (no refresh token to renew this one,
  so the issued token is persisted client-side via secure storage for reuse across app launches
  instead of going through the normal refresh machinery); `AuthNotifier.signInWithDesktopOAuth`
  and a platform-gated login button (`core/auth/platform_login_support.dart`, following
  `chat_header_overflow_menu.dart::chatHeaderShowsLiveVoice`'s existing precedent for this kind of
  allowlist) complete the flow. 12 new client tests, unverified locally — no Dart/Flutter
  toolchain here, Client CI is the real check per this repo's conventions.
- **J5b. Done, with the interactive-diff/live-terminal parts still deferred.** `aura_hostd` had no
  local surface for a UI to query -- only the outbound WebSocket to the backend and its own CLI --
  so this unit added one: `control_server.dart` (new) binds a loopback-only HTTP server (ephemeral
  port + a random per-run token written to `control.json` in the daemon's config dir) exposing
  `GET /status`, `GET`/`POST /roots` (grant/revoke/trust, the same `WorkspaceStore` the CLI uses),
  `GET /list` and `GET /read` (containment-checked exactly like every other filesystem RPC in this
  daemon -- `WorkspaceRoots.requireContained`, no separate path-handling logic that could bypass
  it), and a two-phase `/pair/start` + `/pair/status` pairing flow. The two-phase split (rather
  than one blocking `/pair` call) exists because a UI in a *different process* needs the code back
  *before* approval happens, not after -- `pairing_client.dart` was refactored to expose
  `startPairing`/`pollPairingOnce` as separate functions for this, with `pairWithCode` (still what
  the CLI uses) now a thin wrapper composing both, unchanged in behavior. `bin/aura_hostd.dart`'s
  `run` command now starts the control server immediately and connects to the backend once
  authenticated -- either already, or later via `/pair/status` reporting approved, without needing
  a restart. On the client: `core/hostd/` (new) -- `HostdControlClient` (HTTP client for the
  control server), `HostdSupervisor` (the first process-supervision code in this app;
  `Process.start`s `aura_hostd run`, captures its stdout/stderr, no existing precedent to follow),
  and Riverpod wiring (`hostd_providers.dart`) including a polled status snapshot and a capped log
  buffer. `HostDaemonPage` (new, reachable from the Engineer history screen) ties it together:
  start/stop, pairing (shows the code in a dialog while polling), root grant/revoke/trust (labeled
  honestly -- "trusted" is the real mechanism that exists today, not a fabricated "YOLO" toggle
  with nothing behind it), and a read-only file browser (`HostFileBrowserPage`, new) reusing
  `SandboxFileView` rather than duplicating it. Both new local-only files
  (`hostd_config_paths.dart`, `hostd_control_client.dart`, `hostd_supervisor.dart`) sit behind the
  `dart.library.ffi` conditional-export pattern `desktop_oauth_client.dart` (Unit 7) already
  established, so the web build never pulls in `dart:io`. 18 new daemon-side tests
  (`control_server_test.dart` plus additions to `pairing_client_test.dart`), unverified locally --
  no Dart toolchain here, `hostd-ci.yml` is the real check. 8 new client-side tests
  (`hostd_control_client_io_test.dart`, `hostd_providers_test.dart`), same caveat, Client CI is the
  real check. **Deliberately deferred, and disclosed rather than faked:** interactive diff review
  of a *pending* agent change (already covered where it matters -- chat's
  `engineer_confirmation_card` + `SandboxDiffView`, before a write lands) and a real live-streaming
  terminal panel (the daemon-to-backend streaming Unit 4 built has no client consumer yet, and
  wasn't the target here); process-supervision restart-on-crash logic (this unit's `HostdSupervisor`
  starts and stops a process but does not auto-restart it); and a bundled `aura_hostd` binary
  (there is none -- the person configures the path themselves, the same disclosed-until-configured
  shape `google_desktop_client_id` already uses).
- **J5c. Done — packaging/CI, the remaining piece of Unit 9.** `hostd-ci.yml` has gated every
  `aura_hostd` change since J3 and `client-ci.yml`'s `analyze-and-test`/`build-android` jobs have
  gated `aura_client` since before this phase; what was still open was a CI build gate for the
  Linux desktop target itself and build scripts to produce one outside CI. Added: a `build-linux`
  job in `client-ci.yml` (installs `ninja-build`/`libgtk-3-dev`, `flutter config
  --enable-linux-desktop`, then `flutter build linux --release` against a placeholder
  `BACKEND_URL` -- build-only, unsigned, same pattern `build-android` already uses, so a
  Linux-only regression is caught in CI instead of only at release time) and
  `scripts/deploy_client_desktop.sh` (Linux, plus macOS when run on Darwin) /
  `scripts/deploy_client_desktop.ps1` (Windows), both resolving `BACKEND_URL` from `deploy/.env`
  through the same `client_backend_url.sh`/`.ps1` helpers `deploy_client.{sh,ps1}` already use,
  with thin root-level wrappers matching the existing `deploy_client.sh`/`.ps1` pattern.
  Deliberately out of scope, matching J5b's own disclosed cut: neither script builds or bundles
  `aura_hostd` -- a person still builds it themselves (`dart compile exe`) and points the client's
  daemon executable-path setting at the result. No macOS/Windows CI job was added (Linux is the
  practical desktop target this phase actually targets, and hosted macOS/Windows runners cost
  meaningfully more per minute than the Linux runner `build-android`/`build-linux` share) -- add
  one if and when macOS/Windows desktop support is actually claimed. Unverified locally like every
  other Dart/Flutter change in this phase (no toolchain here); `client-ci.yml`'s new job running
  green is the real verification.

**Exit criteria (not yet met).** An engineer-capable user can pair a real desktop app and have the
agent read, edit, and run commands against a real local project, with a path outside every granted
workspace root refused even in the daemon's most permissive consent mode — the containment half is
implemented and unit-tested (J3), real auth exists end to end for both the daemon (J4) and the app
itself (J5a), the app can now supervise, pair, and grant workspace roots on the daemon without
touching a terminal (J5b), and the Linux desktop build now has its own CI gate and build scripts
(J5c), and the desktop app's own login now has a real Google Cloud OAuth client registered and
hardcoded (2026-09-10) -- corrected once already against a real run (the client secret fix
above), which is real evidence the flow gets far enough to talk to Google, not proof it's
finished. This is still **not proven** end-to-end: past that one confirmed fix, none of
J5a/J5b/J5c's new code has been watched complete a full run in this environment -- only reacted
to the one error a real attempt surfaced. This environment has no Dart/Flutter toolchain (confirmed:
`dart`/`flutter` both absent), so per this repo's own testing conventions, no client-side claim
belongs here until CI has actually run it — that applies to `aura_hostd`'s own test suite
(`hostd-ci.yml`) exactly as much as it does to
`aura_client`'s.

### Phase K — Desktop IDE (Windows-first)

**Phase 0 production audit (2026-09-25):** the "AURA Desktop IDE — Production & Professional UX
Upgrade Plan" mandates a read-only audit before any redesign work. That audit is done — see
[`reports/desktop-ide-phase0-audit-2026-09-25.md`](reports/desktop-ide-phase0-audit-2026-09-25.md).
Headline corrections to this section: D4 (Terminal/PTY) is further along than the phase map below
implies (a real Win32 ConPTY exists, Windows-only, unexercised by CI); D5 (Git/Diff) and large
parts of D6–D9 (AI coding layer, debug, agent workspace, control center) already have real,
non-mocked implementations that this document's phase map had not caught up to. Conversely, two
D1 exit criteria are not actually met: layout does not persist across restart despite a migrated
Drift table for it, and sidebars/bottom panel are not resizable despite the setters existing.
**Fixed (2026-09-25)**, per the audit report's own update note: `LayoutNotifier` now persists to
`desktopLayoutState` and `DesktopShell` has real drag-to-resize handles. Read the audit before
treating any D-phase status below as current.

**Status:** D0 architecture phase done (ADR-015). D1–D3 scaffolding exists and has been fixed to
compile and wired end-to-end (router → `DesktopShell` → Explorer/editor/quick-open/session
persistence), but **this is not yet proven** — this environment has no Flutter/Dart toolchain, so
none of it has been run through `flutter analyze`/`flutter test`/`flutter build windows` (the new
`build-windows` job in `client-ci.yml`, `continue-on-error: true` until proven). Do not treat D1–D3
as done until that job (or a local Windows run) is green; the exit criteria below still name the
real bar.

Known compile/logic bugs fixed in this pass (kept here since they're the kind of thing that would
otherwise re-appear if re-scaffolded): `router.dart` importing a nonexistent
`desktop/desktop_shell.dart` path and missing page imports; a top-level `static` field (illegal
outside a class) in `flutter_code_editor_provider.dart`; several files missing
`dart:async`/`flutter/foundation.dart`/`package:drift/drift.dart`/`package:collection/collection.dart`
imports; two barrel files (`desktop/state.dart`, `desktop/events.dart`) exporting paths one
directory level too shallow; a `sealed class IdeEvent` subclassed from outside its own library file
(illegal for sealed classes); an ambiguous-import collision between two same-named `HostdRoot`
classes; `WorkspaceNotifier` subscribing to its own `WorkspaceOpened` event and re-publishing it —
an infinite async loop; `DesktopShell` rendering its entire body (editor, sidebars, bottom panel)
inside a `SizedBox` sized to `titleBarHeight` (36px); the primary sidebar showing the routed
welcome/manager page instead of a file tree; `HostNotifier` checking a `status['reachable']` key
`/status` never returns; and two `.replace()` Drift calls that would have thrown on any workspace
without every column supplied. Also newly built: `desktop/explorer/` (was entirely missing) and
`desktop/host/host_panel.dart`, and `EditorNotifier` session persistence to `DesktopEditorState`
(was a stub).

The Engineer agent already executes on the user's machine via `aura_hostd` (Phase J / ADR-011). The server-side persistent workspace model (ADR-013/014) provides durable per-(user, repo) workspaces. What is missing: a **human-facing IDE** that becomes the control surface for the same workspace model — not a port of the mobile app, but an IDE where AURA is a first-class workspace participant.

Three-surface UX target:
```
             AURA DESKTOP
                  │
     ┌────────────┼────────────┐
     ↓            ↓            ↓
 WORKSPACE    INTELLIGENCE   CONTROL
     │            │            │
 Editor         AURA        Approvals
 Files          Agents      Host
 Git            Plans       Processes
 Terminal       Context     Tasks
 Tests          Memory      Security
```

D0–D3 establishes **Workspace** (editor, files, layout) with architectural plumbing for Intelligence and Control to integrate naturally in D4+.

Key architectural decisions (ADR-015):
- Dual-shell architecture: mobile keeps `ShellLayout` (`/today`, `/chat`, `/life`, `/you`); Windows gets `DesktopShell` at `/ide/*` with explicit preference (no forced redirect)
- Filesystem split: human IDE reads via hostd (granted roots), writes via `dart:io` (workspace root); agent uses WebSocket → hostd (containment enforced by daemon)
- Editor abstraction (`EditorProvider`) prevents lock-in; `flutter_code_editor` + `highlight` is initial provider
- Drift v12 local-only tables for desktop session state (workspaces, editor, layout, terminal, agent)
- `AuraIdeDensity` ThemeExtension for IDE-specific metrics (activity bar, sidebar widths, tab height, etc.)
- Desktop state ownership matrix: Server-owned / Desktop-local / Host-owned — Riverpod notifiers per domain
- IDE event model (`IdeEvent` stream): workspace.opened, file.changed, agent.*, approval.requested, git.changed, diagnostic.added
- Shared containment test vectors (client = UX validation only; hostd = authoritative)

Phase map:
```
D0 — Architecture doc + ADR-015 + containment test vectors
D1 — Shell + State + Theme + Abstractions (Drift v12, AuraPlatform, DesktopShell, routing, editor abstraction, state notifiers, event bus, Windows CI)
D2 — Workspace + Explorer (repository, welcome, manager, hostd-backed tree, HostPanel extraction)
D3 — Editor (immutable EditorTab, tab bar, EditorProvider impl, file I/O, quick open, shortcuts, persistence)
D4 — Terminal + PTY
D5 — Git + Diff
D6 — AURA AI Coding Layer (inline, diff, multi-file)
D7 — Tests + Problems + Debug
D8 — Agent Workspace (persistent per-repo)
D9 — Multi-Agent Control Center
D10 — Personal AI OS Desktop
```

Migration/reuse: `HostdControlClient` (explorer read + grant), `HostDaemonPage` (full settings → slim `HostPanel`), `HostFileBrowserPage` (delegate to shared `ExplorerService`), `ChatPage` (agent panel in D6), `engineer_run_repository` (task center D8).

**Exit criteria (D0–D3):**
- D0: Architecture doc reviewed; ADR-015 merged; containment test vectors passing in client and hostd test suites
- D1: Windows build shows persistent activity bar + sidebars + bottom + status; desktop preference works; `flutter analyze` clean on Linux and Windows; Drift v12 migration runs; editor abstraction compiles
- D2: Open folder → tree matches disk; granting root updates hostd; recent workspaces survive restart; hostd down → explorer reads via `dart:io` (read-only banner)
- D3: Multiple files open; syntax highlight; dirty state + save; session restore after restart; unsaved close prompts; quick open works; shortcuts work; `flutter analyze` clean

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
| Groundedness / citation precision | **Groundedness 0.94, citation coverage 0.83** (23-case `docs_corpus` suite, `ContextGroundedProvider`, gated in CI) — measures whether the retrieval → context-fencing → citation-resolution pipeline stays intact, not real-model answer quality | Phase A3 |
| Single-turn latency, real data tier | **p50 56.3 s / p95 70.5 s**, full free-text answer, 3 CPU cores, `qwen2.5:3b-instruct-q4_K_M` ([A4 report](reports/phase-a4-load-test.md)) — not comparable to A1's 2-output-token warm/cold figures | Phase A4, measured |
| Concurrency ceiling | **Flat throughput (~0.04–0.06 turns/s) from 1 to 8 concurrent requests** — `OLLAMA_NUM_PARALLEL=1` is the binding constraint, confirmed rather than assumed ([A4 report](reports/phase-a4-load-test.md)); 478 turns/s remains an echo-provider artifact, not this number | Phase A4, measured |
| Gateway image size | **2.34 GB**, down from 8.88 GB once CUDA was dropped ([A1 report](reports/phase-a1-first-boot.md)) | Phase A1 finding 2 |
| Tool success rate | **Live** via `GET /api/v3/admin/scorecard` (`TelemetrySink.recent_summary`); no real production traffic yet to report a baseline number from | Phase G1, mechanism done |
| Task completion rate | **100%** (50-case `agent_tasks` suite, `EchoProvider`, gated in CI) — the evaluator was broken (four stacked bugs) until this pass, and 8 cases' dataset wording was wrong until a follow-up pass corrected it; the number reflects round-trip completion, not real-model quality | Phase C3 |
| Scanned-document OCR | **Real**, Tesseract, proven on a genuinely scanned PDF end to end through `POST /knowledge/documents/upload`; no vision model (image/chart understanding) | Phase F1 |
| Voice (STT/TTS) | **Real**, self-hosted (`faster-whisper` / Piper primary, espeak-ng fallback), proven with a real synthesize→transcribe round trip through `POST /voice/speak` and `/voice/transcribe` | Phase F2 |
| Live voice duplex | **Scaffolding** — WS session + engine + Flutter `/voice`; not device-proven; load round-trip open | Phase H |
| Transcript NLP | **Scaffolding** — `aura_core/nlp/` normalize + shallow intent; quality on real ASR unproven | Phase I |
| Persistent engineer workspaces | **Built, not proven** — Units 1–11 on `main` (`91b6d5c` / #70; panel + dev parity in #72); VPS still `AURA_SANDBOX_ENABLED=false`, no sandbox profile; 10 live checks open | Phase J (workspaces), ADR-013/014 |
| Desktop Engineer (`aura_hostd`) | **In tree, partial** — Units 1–9 done with disclosed cuts (live xterm / restart-on-crash / a bundled `aura_hostd` binary / interactive tiered consent remain open); Desktop OAuth client now registered (2026-09-10); still unproven end to end — no Dart/Flutter toolchain run anywhere but CI ([ADR-011](architecture/011-desktop-engineer-agent.md)). **Correction (2026-09-25):** the "real PTY... remain open" half of this line is now stale — Phase K/D4 added a genuine Win32 ConPTY (`aura_hostd/lib/src/pty_session.dart`), post-dating this row. **Further update (2026-09-25):** a POSIX backend (`posix_pty_session.dart`, `posix_openpt`-based) now exists for Linux/macOS too, behind a shared `PtySessionInterface`, with OS-aware shell selection (`$SHELL`, falling back to `/bin/zsh`/`/bin/bash`) — but it is explicitly **UNTESTED**, written with no Dart toolchain available to compile or run it; a real `ubuntu-latest`/macOS CI run (`posix_pty_session_test.dart` exists to prove it once CI reaches a runner again, GAP-043 permitting) is what would move this to `VERIFIED`, not this note. See the [Desktop IDE Phase 0 audit](reports/desktop-ide-phase0-audit-2026-09-25.md) §3. | Phase J (Desktop) |
| Multi-model routing | **Proven** against two real, simultaneously-running local models (135M and 3B params) — `FASTEST`/`QUALITY_FIRST`/`BALANCED`/failover all route on real measured behaviour; `CHEAPEST` ties (both cost `$0`) | Phase G2, `tests/load/test_multi_model_routing.py` |
| Restore verified | **Yes for all three stores** — Postgres, Qdrant and Redis each restored from the `backup` service's own output ([A2 report](reports/phase-a2-restore-drill.md)); offsite copy and `restore.sh` still unexercised | Phase A2 |
| Production deployments | **1 and counting** — the real VPS, 17 services, verified live over HTTPS on 2026-08-23 | Phase A1 |

## 6. Production program (2026-09-25)

The 26-phase production plan (GPU inference fabric, 50→100-user capacity, RAG/Memory/Agents 2.0,
multimodal, voice, MLOps, HA/DR, canary launch) lives in
[`docs/production/MASTER_PRODUCTION_ROADMAP.md`](production/MASTER_PRODUCTION_ROADMAP.md). This
document stays the evidence ledger for Phases A–K; the production program's own ledger is the
machine-checked [`gap_register.yaml`](production/gap_register.yaml) (rendered as
[`GAP_REGISTER.md`](production/GAP_REGISTER.md)), which re-verified the 2026-09-13 audit
([`FULL_SYSTEM_ARCHITECTURE_AUDIT.md`](reports/FULL_SYSTEM_ARCHITECTURE_AUDIT.md)) against the tree
and corrects it where it was stale (PostgresPool lock, personal-OS update/delete) or wrong in the
other direction (ingestion idempotency — see below).

What the first wave landed, and how far each item is proven. **None of it has run in CI**: every
GitHub Actions job on the repo has died before reaching a runner since 2026-09-22 (GAP-043), so the
evidence below is local suite runs (pytest 2610 passed / 49 skipped / 0 failed, legacy unittest 100
OK) plus the real-infrastructure runs named per row.

| Item | State | Evidence / what is still missing |
| --- | --- | --- |
| Baseline restored after the 2026-09-23 direct pushes broke `Container` construction (~200 errors) | **Merged** (#96, `11abb50`) | Branch protection on `main` still off (GAP-005) |
| P1.2 server-side encryption at rest — memory content, personal-OS v3 fields | **Built, not proven** | Real Postgres 16 round-trip + psql ciphertext check; not deployed; ADR-004 "Encryption at rest" lists what stays plaintext. Not end-to-end. |
| P1.3 backup encryption (age), manifests, GFS retention, offsite verification, drill timing | **Built, not proven** | 56-case shell test with real `age`, shellcheck clean; no encrypted cycle, upload or drill on the VPS. **Deploy precondition:** the age public key must be on the VPS first or backups stop (fail-closed, alerted). |
| P1.4 `Idempotency-Key` on side-effecting v3/admin routes | **Built, not proven** | Real Postgres 16: 20-way concurrent claim, two gateway processes sharing one table; no client sends the header yet |
| Ingestion idempotency — **was a cross-user leak** | **Fixed, not deployed** | Document ids were `hash(title)` (per-process salted, not owner-scoped): same-titled uploads by two users became versions of one document owned by the first. 11 regression tests; GAP-004 |
| GAP-009 `SecretManager` "AES-256-CTR" never used its key | **Fixed** (`b16d168`) | AES-256-GCM; wrong-key/tamper/rotation tests fail on the old code |
| P2 capability routing across model pools, admission control (priority queue, 503/429 + Retry-After), latency decomposition, opt-in vLLM GPU profile | **Built, not proven** | 48 tests with FrozenClock; no GPU host exists (GAP-012); bounds the Ollama queue, adds no throughput (GAP-011) |
| P1.5 security regression suite — 257 tests; **11 live vulnerabilities fixed** plus the concurrent-taint gap | **Built, not proven** | Session overwrite via foreign `session_id`, refresh-token double spend, mock attestation in production, WebSocket reauth as another user, GitHub allowlist bypass via unescaped paths, SSRF gaps, `git_push` to the default branch via refspecs, `ingest_pdf_task` path escape; each with a test that failed first (GAP-014, GAP-048) |
| P3 1→100-user load harness | **Built** | In-process echo self-validation only — explicitly not a capacity number; the 50/75/100-user gates need the GPU deployment (GAP-041) |
| P14 evaluation gates PRs | **Built, not proven** | Pass on the tree, fail on a deliberate ranking regression; `eval-ci.yml` had been invalid YAML and never ran; blocks nothing until branch protection requires it |

### Wave 2 (2026-09-25)

Knowledge graph, agent platform, Flutter/admin reach, and the fine-tuning honesty fix. Local suite
on the merged tree: **pytest 2697 passed / 56 skipped / 0 failed**, legacy unittest 100 OK; still no
CI run (GAP-043).

| Item | State | Evidence / what is still missing |
| --- | --- | --- |
| GAP-018 — training failures recorded as SUCCEEDED | **Fixed, not deployed** | Non-training outcomes are `NOT_TRAINED`, register nothing and cannot be promoted; tests written first |
| GAP-017 — trainer never changed weights | **Mechanism proven** | Real PEFT LoRA or a hard failure (no fake fallback). First weight-changing run in the repo: `pytest tests/training -m training` on `tiny-random-gpt2`, LoRA delta L2 2.56, loss 6.90 → 6.81, logits differ. Nothing trained on the production base model; quality unmeasured |
| Phase 11 knowledge graph (GAP-023) | **Built, not proven** | Provenance-gated pipeline, delete/supersede lifecycle, relational routing, owner-scoped read API ([ADR-005](architecture/005-hybrid-retrieval.md) §Knowledge graph); real Postgres 16 store/lifecycle/migration tests pass; extraction quality on real documents unmeasured |
| Phase 7 agent platform (GAP-024) | **Built, not proven** | One bounded loop for every persona with five budgets, durable content-free runs, per-(user, scope) memory, run API, `agent_runs` eval suite ([ADR-016](architecture/016-agent-platform.md)); no production traffic yet |
| Phase 16 client reach (GAP-013) | **In progress** | Generated, test-gated [API reachability matrix](guides/api-reachability.md): 74 of 97 routes called, 16 uncalled and triaged. Flutter sends `Idempotency-Key` and handles overload/`Retry-After` — **not compiled or tested** (no Dart toolchain here, Client CI blocked) |
| Phase 17 admin (GAP-007) | **Open** | No admin Playwright specs this wave; node and Chromium are available for the next |

---

Related: [Production Readiness Score](reports/AURA_AI_PRODUCTION_READINESS_SCORE.md) ·
[Current Architecture](reports/AURA_AI_CURRENT_ARCHITECTURE.md) ·
[Architecture Overview](architecture/00-overview.md) ·
[Missing Components](analysis/03-missing-components-report.md) ·
[V2 Blueprint](archive/legacy-doc/V2_BLUEPRINT.md)
