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
- **No OCR, no vision, no voice.** Scanned pages are indexed as honest placeholders naming the
  reason they could not be read. Audio and video have no extraction at all.
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
| A4 | Load test with the real data tier. Add `aura_backend/tests/load/` (referenced by the missing-components report, absent from the tree), running against Postgres, Qdrant, Redis and Ollama in path. | Extend the harness in `tests/benchmarks/bench_pipeline.py` — its conditions, not its numbers. |

**Exit criteria.** A real recall@10 / nDCG / groundedness figure per RAG strategy, on a real
corpus. A real p95 with a model in the path. A documented concurrency ceiling naming
`OLLAMA_NUM_PARALLEL` as the binding constraint. One successful restore.

**Non-goal.** Tuning. Phase A measures; Phase G optimises against what it measured.

### Phase B — Close the last legacy seams

Small, and it removes ambiguity that Phase C would otherwise inherit.

- **B1.** Delete `aura_backend/main.py` and the dead client path at
  `aura_client/lib/core/ai/online_agents/online_agent_manager.dart:48`, which posts to
  `/api/chat/agent` — a route `aura_service.py` does not mount and
  `tests/unit/test_v1_locked_routes.py` asserts returns 404. The routes are already unreachable;
  this deletes the code that still pretends otherwise. Confirm no test or script imports them
  first, and keep the lock test.
- **B2.** Move `UserMemories` onto the `/api/v3/sync` cursor protocol. This is the known gap
  deliberately deferred in `Doc/V2_BLUEPRINT.md` Phase 1 and documented in
  `core/sync/sync_client.dart`; it is the last table on a bespoke protocol.
- **B3.** Record a decision on `api/v2/{auth,sync,insights}`. They are mounted by `aura_service.py`
  as platform adapters outside the core. Either fold them into `/api/v3` or document the boundary
  as permanent — do not leave it implicit, because Phase C needs to know where new endpoints go.

### Phase C — Agent platform

- **C1.** Replace the `AGENT_PROMPT_IDS` dict with an agent registry: identity, prompt, allowed
  tools, required capabilities, risk ceiling, memory scope. Bind it through the existing
  `aura_core/tools/policy.py` and the risk/confirmation machinery in `aura_core/tools/registry.py`.
  Do not introduce a second permission model beside the one the tool registry already enforces.
- **C2.** Add server-side CRUD tools for tasks, calendar, finance and health against Postgres, so
  the kernel can act rather than only propose. Keep `propose_*` for high-risk writes: the
  confirmation path through `personal_os.py` and the client's `pending_actions_card.dart` works
  and should not be rebuilt.

**Exit criteria.** An agent's tool set is data, not code. A tool call outside an agent's grant
fails closed and is visible in `PostgresAuditSink`. Task-completion rate is measurable through the
Phase A harness.

### Phase D — Projects, Goals, and prioritisation

The deliberately scoped-down Business OS. These two domains earn their place because the kernel
needs them to answer "what should I work on"; leads and revenue do not (see §4).

- **D1.** `Project` and `Goal` in `infrastructure/db/models.py` with `SyncableMixin`, matching Drift
  tables in `aura_client/lib/core/database/database.dart`, carried by the existing cursor sync.
  Flutter feature modules follow the `features/tasks/` layout.
- **D2.** Teach `aura_core/context/engine.py` to read goals into the context snapshot.
- **D3.** A prioritisation tool ranking tasks by deadline, goal alignment, effort and dependencies,
  and a calendar slot-finding tool. Both as registry tools, so the kernel plans *with* them rather
  than around them.

### Phase E — Proactive intelligence

- **E1.** Durable event delivery for reactions that must not be lost: a Postgres outbox drained by
  Celery. `InMemoryEventBus` stays for metrics, audit and cache invalidation — that is what its
  docstring scopes it to, and it is correct for those.
- **E2.** A trigger engine over domain events, plus a daily briefing.
- **E3.** Upgrade `services/insights.py` from fixed rules to kernel-generated insights with
  citations — gated by the Phase A corpus, so the upgrade is provably not a regression. Rules beat
  a model call on cost, latency and reliability today; the burden of proof is on the replacement.

### Phase F — Multimodal and voice

- **F1.** OCR and a vision model, replacing the placeholder chunks in
  `aura_core/knowledge/documents.py`. This is the one capability the readiness report states
  outright does not exist, and the `multimodal` retrieval strategy currently filters page
  structure rather than image content.
- **F2.** Server-side STT/TTS, keeping inference on our own infrastructure per the self-hosting
  guarantee.

### Phase G — Continuous intelligence

- **G1.** The scorecard below as a live dashboard fed by real evaluation runs and `TurnTelemetry`,
  not hand-maintained numbers.
- **G2.** Multi-model routing. `aura_core/llm/router.py` implements five policies but has only ever
  run against Ollama and an echo provider — serve a second model before claiming routing works.

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
| Single-turn latency, real data tier | **~5.3 s warm / 20.2 s cold**, 2 output tokens, 3 CPU cores ([A1 report](reports/phase-a1-first-boot.md)) | Phase A4 for p95 under load |
| Concurrency ceiling | Unmeasured (478 turns/s is an echo provider) | Phase A4 |
| Gateway image size | **2.34 GB**, down from 8.88 GB once CUDA was dropped ([A1 report](reports/phase-a1-first-boot.md)) | Phase A1 finding 2 |
| Tool success rate | Unmeasured | Phase C + `ToolTelemetry` |
| Task completion rate | Unmeasured | Phase C |
| Restore verified | **Yes for all three stores** — Postgres, Qdrant and Redis each restored from the `backup` service's own output ([A2 report](reports/phase-a2-restore-drill.md)); offsite copy and `restore.sh` still unexercised | Phase A2 |
| Production deployments | **1 and counting** — the real VPS, 17 services, verified live over HTTPS on 2026-08-23 | Phase A1 |

---

Related: [Production Readiness Score](../AURA_AI_PRODUCTION_READINESS_SCORE.md) ·
[Current Architecture](../AURA_AI_CURRENT_ARCHITECTURE.md) ·
[Architecture Overview](architecture/00-overview.md) ·
[Missing Components](analysis/03-missing-components-report.md) ·
[V2 Blueprint](../Doc/V2_BLUEPRINT.md)
