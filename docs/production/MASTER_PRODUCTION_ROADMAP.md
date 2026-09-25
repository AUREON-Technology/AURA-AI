# AURA Master Production Roadmap

The plan that takes AURA from its current state (a well-architected, honestly documented,
single-VPS, CPU-inference platform that serves one AI request at a time) to a production service
for **50+ concurrent users**. It covers 27 phases (0–26). Each one is condensed to its goal, the
work, its exit criteria, and the gaps it closes from [gap_register.yaml](gap_register.yaml).

Baseline: tree `11abb50`, 2026-09-24.

## How this relates to `docs/AURA_UPGRADE_ROADMAP.md`

[`docs/AURA_UPGRADE_ROADMAP.md`](../AURA_UPGRADE_ROADMAP.md) **remains the evidence ledger for
Phases A–K** (Evidence, legacy seams, agent platform, projects/goals, proactivity, multimodal,
continuous intelligence, live voice, transcript NLP, engineer workspaces and desktop engineer, and
the desktop IDE). What it records as done, built-not-proven or open stays authoritative for that
work, and this document doesn't restate or re-score it. This document is the **forward plan for
production readiness**. It sits on top of that ledger:

- Where a phase here continues an A–K track, it names the letter (e.g. Phase 8 continues F/H;
  Phase 18 continues J/K) and inherits its standing rule.
- New evidence for Phases A–K is still recorded in `AURA_UPGRADE_ROADMAP.md`. New evidence for
  Phases 0–26 goes in `docs/reports/` and is linked from the gap register entry it closes.
- The coordinator reconciles the two documents. This file doesn't edit the upgrade roadmap.

Companion documents in this directory: [GAP_REGISTER.md](GAP_REGISTER.md) and
[gap_register.yaml](gap_register.yaml) (source of truth), [MODEL_REGISTRY.md](MODEL_REGISTRY.md),
[PRODUCTION_SLO.md](PRODUCTION_SLO.md), [DEPLOYMENT_TOPOLOGY.md](DEPLOYMENT_TOPOLOGY.md),
[DISASTER_RECOVERY.md](DISASTER_RECOVERY.md), [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) and
[VERSION_FREEZE.md](VERSION_FREEZE.md).

## Standing rules (binding on every phase)

1. **No phase is done without the evidence its exit criteria name.** "Built" and "tested" are not
   "proven". Every number is published together with the conditions it was measured under (the
   inherited rule from the upgrade roadmap).
2. **Evolve, don't rewrite.** Hexagonal layering (domain/ports → engines → adapters, with
   `container.py` as the only wiring point) is enforced by `tests/architecture/`. New capability
   goes through `AIKernel` as a new adapter or configuration, never beside it.
3. **All models are self-hosted.** Only the `echo`, `openai_compatible` and `ollama` provider kinds
   exist, and `tests/architecture/test_no_external_inference.py` keeps it that way.
4. **No model execution on the Flutter client.** `pubspec.yaml` is fitness-tested.
5. **No hosted-AI fallback.** That includes rented off-host inference (the HF Space `hfspace`
   path) in production, unless an ADR explicitly accepts it (GAP-034).
6. **Planning/execution is the only path that runs tools** (ADR-006). A model's `tool_calls` stay
   informational. Trust channels, taint and confirmation apply to every new tool.
7. **The default `pytest` suite needs no Docker, network or model.** Real-infrastructure proof
   lives in `tests/load` (opt-in) and in reports.
8. **No Kubernetes, microservices or distributed database** until a load report shows the single
   control plane is the bottleneck.

## Phase overview

| # | Phase | Continues | Key gaps | Depends on |
| --- | --- | --- | --- | --- |
| 0 | Baseline freeze and gap ledger | A | all (seeding) | — |
| 1 | Correctness and security blockers | G | 005, 009, 018, 043, 008, 010, 027 | 0 |
| 2 | GPU inference plane (vLLM, specialized pools) | G2 | 011, 012, 030, 031, 035 | 1 |
| 3 | 50+ user scalability (load matrix 1→100) | A4 | 041, 011, 036, 037 | 2 |
| 4 | RAG 2.0 | A3 | 021, 031 | 2, 14 |
| 5 | Memory 2.0 | — | 016, 024 | 1 |
| 6 | Personal OS | C/D | 013, 016 | 5 |
| 7 | Agent platform 2.0 | C | 014, 024 | 2 |
| 8 | Multimodal (vision/video) | F | 019 | 2 |
| 9 | Voice 2.0 | F/H | 020 | 2 |
| 10 | NLP | I | — | 9 |
| 11 | Knowledge graph | — | 023 | 4 |
| 12 | Proactivity 2.0 | E | 008 | 7 |
| 13 | Learning / MLOps | G | 017, 018 | 2, 14 |
| 14 | Evaluation 2.0 | A3 | 006, 021, 022 | 2 |
| 15 | Observability 2.0 | G1 | 036, 037 | 1 |
| 16 | Flutter completion | — | 013, 044 | 1 |
| 17 | Admin control plane | — | 007 | 1 |
| 18 | Desktop engineer | J/K | 025, 026, 010 | 1 |
| 19 | Deployment hardening | A1 | 027, 029, 032, 034 | 2 |
| 20 | HA / DR | A2 | 028, 016, 029 | 19 |
| 21 | Security hardening | — | 009, 014, 016, 034, 045 | 1 |
| 22 | Model supply chain | — | 030, 031, 032, 042 | 2 |
| 23 | Production readiness testing | — | 041, 028 | 3–22 |
| 24 | Canary launch | — | — | 23 |
| 25 | SLOs in force | — | 036 | 15, 24 |
| 26 | Continuous improvement | — | — | 25 |

---

## Phase 0 — Baseline freeze and gap ledger

**Status: in progress.** This directory is its deliverable, and its exit criteria aren't met yet.

- **Goal:** one verified, machine-checkable statement of where AURA is, so no later phase plans
  against a stale claim.
- **Work:** restore a green default suite (done in `11abb50`, merged via PR #96). Seed
  `gap_register.yaml` from the 2026-09-13 audit, with every finding re-verified against the current
  tree. Schema-test the register (`tests/architecture/test_gap_register_schema.py`). Freeze
  versions (VERSION_FREEZE.md). Write the model registry, proposed SLOs, topology, DR and release
  checklist.
- **Exit criteria:** the register passes its schema test in CI. Every stale audit finding is marked
  `closed_stale` with evidence (GAP-001..004). Version freeze and model registry are committed.
  **CI actually runs on `main`** (GAP-043), so "passes in CI" is observable. The coordinator has
  reconciled the register with `AURA_UPGRADE_ROADMAP.md`.

## Phase 1 — Correctness and security blockers

- **Goal:** nothing false, broken or unguarded ships from here on.
- **Work:** get GitHub Actions executing again (GAP-043). Protect `main` with required checks
  (GAP-005). Delete or fix `SecretManager`'s key-independent "encryption" (GAP-009). Make trainer
  failures fail the job (GAP-018). Review, test and wire, or delete, the unreviewed 43a2d5c
  packages (GAP-008). Remove the committed binaries and logs (GAP-010). Isolate dev inference
  from prod (GAP-027). Fix the worktree-path fitness-test fragility (GAP-040).
- **Exit criteria:** every P0/P1 gap in the `process/*`, `security/*` and `learning/trainer-*`
  components is `proven`, with linked green CI runs. No direct pushes to `main` since protection
  was enabled.

## Phase 2 — GPU inference plane: vLLM with specialized model pools

- **Goal:** remove the one-generation-at-a-time ceiling without changing the kernel.
- **Work:** provision dedicated GPU host(s) (DEPLOYMENT_TOPOLOGY.md §2). Run digest-pinned vLLM
  pools (chat/planning, coder, vision, embed/rerank), each registered as an `openai_compatible`
  provider with its own `quality_tier`/`max_concurrency`. Bake-off the MODEL_REGISTRY candidates
  (Qwen3-30B-A3B-Instruct-2507, Qwen3-Next-80B-A3B, Qwen3-Coder, Qwen3-Embedding/Reranker) on
  AURA's own suites. Add per-provider circuit settings (GAP-035). Keep CPU Ollama as the local
  last resort. Re-size `max_output_tokens`/timeouts for the new throughput.
- **Exit criteria:** at least one pool serves production traffic with a pinned model revision and
  a pinned vLLM digest. The MODEL_REGISTRY admission rule is satisfied for it. Router failover to
  CPU Ollama is proven against the pool going down (in the style of
  `tests/load/test_multi_model_routing.py`, with a report). GAP-011/012 are `proven`.

## Phase 3 — 50+ user scalability (load matrix 1→100)

- **Goal:** prove, not assume, the concurrency target.
- **Work:** extend `tests/load` into a matrix at 1, 5, 10, 25, 50, 75 and 100 concurrent users
  with a realistic turn mix (chat, RAG, tool, voice). Add a 2 h soak at 50 users. Report p50, p95
  and p99 for TTFT and full answer, error rate, throughput, GPU/CPU/memory, queue depth, and the
  Postgres/Qdrant/Redis saturation signals. Tune `AURA_WORKERS`, pool sizes, Celery concurrency
  and vLLM batch parameters from the data.
- **Exit criteria:** a report in `docs/reports/` showing PRODUCTION_SLO S3/S4/S5 met at 50
  concurrent users on the production topology, with the first saturating component named at 100.
  GAP-041 is `proven`.

## Phase 4 — RAG 2.0

- **Goal:** production-grade retrieval quality, measured on the production embedder.
- **Work:** pin the embedder and reranker (GAP-031). Evaluate a Qwen3-Embedding/Reranker upgrade
  through ADR-007's versioned index and promotion gate (new `IndexIdentity`, reindex, recall gate,
  alias switch, rollback). Build a larger labelled corpus, multilingual including Arabic. Compare
  per-strategy results across `aura_core/rag/{adaptive,agentic,base,expansion,leaves}` (the A3
  open item).
- **Exit criteria:** Recall@10 ≥ 0.80, groundedness ≥ 0.90 and citation precision ≥ 0.90 with the
  production LLM and embedder (PRODUCTION_SLO Q1–Q3), reported per strategy. The default strategy
  is chosen from that data.

## Phase 5 — Memory 2.0

- **Goal:** memory that is confidential and scoped as the product claims.
- **Work:** write the encryption-at-rest decision as an ADR and implement it (GAP-016). Implement
  per-persona `memory_scope` where it's claimed (GAP-024). Verify the consolidation/decay job on
  real Postgres at production volume. Make `forget_source()` coverage verifiable end to end.
- **Exit criteria:** the ADR is merged, the encryption round-trip is tested against real Postgres,
  and isolation tests are green.

## Phase 6 — Personal OS

- **Goal:** Personal OS is reachable and consistent across server tools and the client.
- **Work:** decide how the plaintext v3 `PersonalOsRecord` store and the client-encrypted legacy
  tables coexist. Expose the v3 store to the client (REST route + sync) or document it as tool-only
  (GAP-013). Verify update/delete on real Postgres.
- **Exit criteria:** client reach is shown by Client CI plus a device test, or declared tool-only
  in docs. Real-Postgres CRUD tests are green.

## Phase 7 — Agent platform 2.0

- **Goal:** deliberate autonomy with provable safety.
- **Work:** decide per persona whether to allow multi-iteration autonomy (today only `engineer`,
  GAP-024). Close or pin the concurrent-batch taint window (GAP-014). Add an agent-run entity with
  agent-level telemetry. Evaluate the coder pool on engineer tasks.
- **Exit criteria:** the ADR on autonomy is merged. The taint test is green. Agent task-completion
  is measured with a real model (not `EchoProvider`) and reported.

## Phase 8 — Multimodal (vision and video)

- **Goal:** image and document understanding through the kernel. Continues Phase F.
- **Work:** deploy a VLM pool (Qwen3-VL-8B-Instruct candidate). Build a kernel image path with an
  image trust channel, size limits and taint semantics. Add client upload plumbing. Video only if
  product need is shown.
- **Exit criteria:** a vision turn is served end to end with a pinned model and a vision eval
  report (GAP-019).

## Phase 9 — Voice 2.0

- **Goal:** multilingual, low-latency voice. Continues F/H.
- **Work:** multilingual ASR (Qwen3-ASR or a larger Whisper, Arabic included) with measured WER.
  Neural VAD. Keep Piper for Arabic TTS (Qwen3-TTS lists no Arabic) and evaluate Qwen3-TTS for
  English. Device-prove live duplex (the Phase H open item).
- **Exit criteria:** a WER report in Arabic and English, a live-voice round-trip latency report on
  a real device, and GAP-020 `proven`.

## Phase 10 — NLP

- **Goal:** transcript and text understanding that holds up on real ASR output. Continues Phase I.
- **Work:** evaluate `aura_core/nlp/` intent and normalisation on real transcripts in both
  languages, and replace the shallow intent layer where it fails.
- **Exit criteria:** a labelled intent suite with measured accuracy on real ASR output.

## Phase 11 — Knowledge graph

- **Goal:** turn a built-but-unconsulted graph into a measured capability.
- **Work:** verify `PostgresGraphStore` against real Postgres. Evaluate the graph/adaptive
  strategies against `simple` on a multi-hop suite. Consider `LLMEntityExtractor` on high-trust
  sources once GPU capacity exists.
- **Exit criteria:** GAP-023 `proven`: a real-Postgres test plus a strategy comparison report, and
  the graph strategy enabled only where it wins.

## Phase 12 — Proactivity 2.0

- **Goal:** proactivity beyond the single shipped trigger (`document_quarantined`). Continues E.
- **Work:** either adopt the unreviewed `aura_core/proactive` package through review and tests, or
  replace it (GAP-008). Add triggers through the existing outbox and event bus. Every proactive
  action obeys confirmation policy.
- **Exit criteria:** each new trigger has tests plus an opt-out, and the outbox's no-loss property
  is shown under a worker crash on real Postgres (PRODUCTION_SLO Z3).

## Phase 13 — Learning and MLOps

- **Goal:** either real, gated fine-tuning or an honest relabel.
- **Work:** fix `LocalLoraTrainer` to use `FineTuneJobSpec`'s real fields. Load the pinned base
  model (not a random classifier). Train LoRA/QLoRA on the GPU host. Evaluate against production
  through `RegressionGate`, and promote only through `ApprovalService` and the model registry,
  with rollback (GAP-017/018). Otherwise relabel "learning" as dataset preparation plus approval
  everywhere.
- **Exit criteria:** a real run report (base revision, dataset digest, weight delta, eval delta,
  approval record), or a merged relabel.

## Phase 14 — Evaluation 2.0

- **Goal:** evaluation that gates what ships.
- **Work:** make `eval-ci.yml` PR-triggered for the core paths (GAP-006). Run a scheduled
  real-model eval against the GPU pools (GAP-021). Build guard precision/false-positive suites
  (GAP-022). Keep the LLM judge advisory, and gate on deterministic metrics.
- **Exit criteria:** a deliberately regressing PR is shown blocked. Scheduled real-model runs
  publish `aura_eval_score`.

## Phase 15 — Observability 2.0

- **Goal:** every SLO is measurable.
- **Work:** HTTP request metrics and an external blackbox probe (GAP-036). Widen the histogram
  buckets (GAP-037). Add an outbox-age gauge. Add GPU exporters (DCGM) on inference hosts.
  Burn-rate alerts. Keep the content-free telemetry rule (ADR-009).
- **Exit criteria:** every PRODUCTION_SLO row has status `measurable` or better, on live
  dashboards.

## Phase 16 — Flutter completion

- **Goal:** the client reaches every user-facing capability.
- **Work:** a route-inventory test (GAP-013). Client plumbing for learning feedback, insights and
  personal-OS v3 where they're user-facing. Finish the design-system migration. Pin the release
  toolchain (GAP-044).
- **Exit criteria:** the inventory shows no unconsumed user-facing route. Client CI and a release
  build use the same pinned Flutter.

## Phase 17 — Admin control plane

- **Goal:** an ops console fit for production use.
- **Work:** PR-triggered Admin CI with mock-gateway Playwright specs for all 9 console areas
  (GAP-007). Pool and model management for vLLM. Audit views for tool executions.
- **Exit criteria:** 9/9 areas covered by specs that run on PRs.

## Phase 18 — Desktop engineer

- **Goal:** prove the Engineer agent. Continues J/K.
- **Work:** run `deploy/scripts/verify-engineer-workspaces.sh` on the VPS (GAP-025). Build the
  hostd binary in CI instead of committing it (GAP-010). Close the disclosed hostd cuts (GAP-026).
- **Exit criteria:** the workspace report in `docs/reports/` has all checks passing, plus a
  desktop end-to-end report.

## Phase 19 — Deployment hardening

- **Goal:** a reproducible, least-privilege, two-tier deployment.
- **Work:** split the control plane and the GPU hosts over a private link (DEPLOYMENT_TOPOLOGY.md).
  Digest-pin every image (GAP-032). Refuse remote inference in production (GAP-034). Move dev off
  the prod host or give it its own quota (GAP-027). Add a compose-parity fitness test beyond
  `EXEC`.
- **Exit criteria:** the pinning test and the parity test are green. The target topology is
  deployed via `Deploy VPS`.

## Phase 20 — HA and DR

- **Goal:** recover within stated objectives, measured.
- **Work:** encrypted offsite backups. Quarterly drills restoring the offsite copy onto a fresh
  host. WAL archiving if RPO requires it (DISASTER_RECOVERY.md §5). A documented rebuild runbook.
- **Exit criteria:** a drill report with measured RPO/RTO that meet the proposals (GAP-028).

## Phase 21 — Security hardening

- **Goal:** close residual security risk beyond Phase 1.
- **Work:** an encryption-at-rest implementation (GAP-016). A taint-window fix (GAP-014). Action
  SHA pinning (GAP-045). A cross-tenant probe suite on real Postgres (PRODUCTION_SLO Z2). An
  external penetration test. Secret rotation drills.
- **Exit criteria:** zero open P0/P1 security gaps, and the pen-test report plus remediation are
  recorded.

## Phase 22 — Model supply chain

- **Goal:** every model artefact is immutable, licensed and reproducible.
- **Work:** pin model revisions and digests (GAP-030/031). Mirror weights onto the inference host
  and run the Hub in offline mode. Record a license review per model (GAP-042). Add a startup
  check that the loaded digest equals the pinned one.
- **Exit criteria:** MODEL_REGISTRY has no `UNPINNED` production entry, and the startup check is
  tested.

## Phase 23 — Production readiness testing

- **Goal:** one end-to-end rehearsal of production.
- **Work:** the full RELEASE_CHECKLIST on a release candidate. Re-run the Phase 3 load matrix. A
  chaos pass (kill a pool, Postgres failover-by-restore, Redis loss). A DR drill. A security
  review.
- **Exit criteria:** every checklist box ticked with a link.

## Phase 24 — Canary launch

- **Goal:** controlled exposure.
- **Work:** a canary cohort through router configuration and feature flags, with automatic
  rollback triggers tied to SLO burn, and 24 h / 7 d reviews.
- **Exit criteria:** the canary holds SLOs for 7 days at its cohort size, then rolls out to 50+
  users.

## Phase 25 — SLOs in force

- **Goal:** move from proposed SLOs to operated SLOs.
- **Work:** adopt PRODUCTION_SLO targets from measured baselines, with error budgets, a deploy
  freeze policy and on-call routing.
- **Exit criteria:** 30 days of SLO reporting from production.

## Phase 26 — Continuous improvement

- **Goal:** a closed loop from production signals back into planning.
- **Work:** monthly gap-register review. Feedback-driven eval set growth. Quarterly model
  bake-offs through the admission rule. Quarterly DR drills. Version-freeze bump PRs.
- **Exit criteria:** ongoing. Each cycle produces a dated report in `docs/reports/`.

---

## What should NOT be done

- **Don't rewrite** the kernel, container or layering. Extend through adapters and configuration.
- **Don't add a hosted-AI provider or fallback**, and don't enable rented off-host inference in
  production without an ADR.
- **Don't run any model on the Flutter client.**
- **Don't adopt Kubernetes, microservices or a distributed database** before a load report shows
  the control plane is the bottleneck.
- **Don't dispatch model-emitted `tool_calls`**, and don't bypass taint or confirmation for
  convenience.
- **Don't quote in-memory or echo-provider numbers** (4.56 ms p95, 478 turns/s) as production
  performance.
- **Don't call the learning system "training"** until a real weight-changing run is reported.
- **Don't use `docker exec`** for sandboxes, don't add `IMAGES`/`EXEC` to the socket proxy, and
  don't re-add `http2 on;`.
- **Don't push directly to `main`**, and don't merge without green required checks.
- **Don't let the dev stack share production's inference slot.**
- **Don't pull models or images by mutable tag** in production.
- **Don't mark any phase or gap done without its named evidence.**

## Definition of production-ready

Ticked only where evidence exists in-tree today. Everything else is unchecked.

- [x] Self-hosted-only inference enforced by fitness tests. Evidence:
      `aura_backend/tests/architecture/test_no_external_inference.py` (six layers) and
      `aura_core/llm/registry.py`.
- [x] Model-emitted tool calls are never dispatched. Evidence: completion requests are built with
      `tools=()` at `aura_core/kernel/kernel.py:608,1297,1339` (ADR-006).
- [ ] GitHub Actions runs and `main` is protected with required checks (GAP-043, GAP-005)
- [ ] No open P0 or P1 gaps in `gap_register.yaml`
- [ ] 50 concurrent users within TTFT p95 < 2.5 s and answer p95 < 15 s on the production topology (GAP-041)
- [ ] Every PRODUCTION_SLO metric exists and is dashboarded (GAP-036, GAP-037)
- [ ] 30 days of availability ≥ 99.5% and 5xx < 1% measured in production
- [ ] RAG Recall@10 ≥ 0.80, groundedness ≥ 0.90 and citation precision ≥ 0.90 with the production LLM and embedder (GAP-021)
- [ ] Evaluation gates merges (GAP-006)
- [ ] 0 unauthorized tool executions and 0 cross-user leakage shown by tests on real infrastructure plus a production audit review
- [ ] Encryption-at-rest decision implemented, and backups encrypted (GAP-016)
- [ ] Offsite restore drill with measured RPO/RTO (GAP-028)
- [ ] Every production model and image pinned by digest or revision, with licenses reviewed (GAP-030/031/032/042)
- [ ] Learning is either real and gated, or relabelled (GAP-017)
- [ ] Engineer workspaces proven on the VPS (GAP-025)
- [ ] Release checklist completed with links for the launch SHA
- [ ] Canary held SLOs for 7 days
