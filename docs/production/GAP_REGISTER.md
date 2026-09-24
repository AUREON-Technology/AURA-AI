# Gap Register

A rendered summary of [`gap_register.yaml`](gap_register.yaml), which is the source of truth.
If the two disagree, the YAML wins; fix this file. The YAML's schema is enforced by
`aura_backend/tests/architecture/test_gap_register_schema.py`. That test checks that every field is
present, `gap_id`s are unique, severity and status come from the allowed sets, dependencies point
at known gaps, and every `proven` or `closed_stale` entry has `evidence`.

- **Baseline:** tree at `11abb50` (`claude/aura-production-roadmap-5x08so`), verified 2026-09-24.
- **Seeded from:** [`docs/reports/FULL_SYSTEM_ARCHITECTURE_AUDIT.md`](../reports/FULL_SYSTEM_ARCHITECTURE_AUDIT.md)
  (audit baseline `dd88f95`, 2026-09-13). Every audit finding was checked again against the current
  tree before it went in. Findings the tree has since overtaken are recorded as `closed_stale`
  with the evidence, so nobody plans work against them.
- **Plan it serves:** [MASTER_PRODUCTION_ROADMAP.md](MASTER_PRODUCTION_ROADMAP.md). The evidence
  ledger for Phases A–K remains [`docs/AURA_UPGRADE_ROADMAP.md`](../AURA_UPGRADE_ROADMAP.md).

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `open` | Not started, or started with nothing merged. |
| `in_progress` | Work merged or in review, and the exit condition isn't met yet. |
| `built_not_proven` | The code exists and its tests pass, but the named evidence (a live check, a report, a real-infra run) doesn't exist yet. |
| `proven` | The exit condition's evidence exists in-tree and is cited in `evidence`. |
| `closed_stale` | The audit (or an earlier doc) claimed a gap that the current tree no longer has. `evidence` cites the file:line or commit that closed it. |

Severity: **P0** blocks the 50+ concurrent-user target or is a live integrity risk. **P1** must
close before any production claim. **P2** is needed for the full target platform. **P3** covers
hygiene and doc accuracy.

## Where the audit was wrong or stale

| Audit claim (dd88f95) | Current tree (11abb50) | Gap |
| --- | --- | --- |
| `PostgresPool._get_pool()` has no `asyncio.Lock` (R2, top-20 #2) | Lock at `aura_core/adapters/postgres_store.py:98` with a double-checked re-check at `:104-121`. 6-case regression test `tests/unit/test_postgres_pool_concurrency.py`. Commit `12bc502`. | GAP-001 `closed_stale` |
| Personal-OS v3 store is create+list only (§21, top-20 #5) | `update`/`delete` at `aura_core/adapters/postgres_personal_os.py:152-166`, and the `update_*`/`delete_*` tools at `aura_core/tools/records.py:107-145`, both owner- and kind-scoped. Commit `3fbb811`. | GAP-002 `closed_stale` |
| GitHub integration routes have zero Flutter callers (§22) | `github_integration_repository.dart:43,62,81` calls status/connect/disconnect. | GAP-003 `closed_stale` |
| Idempotency of `ingest_document_task` is UNKNOWN (§24, #12) | That task has no caller. The live v3 path is idempotent on content hash (`aura_core/knowledge/engine.py:168-183`, tested at `tests/unit/test_knowledge.py:144`). | GAP-004 `closed_stale` (the dead code is tracked as GAP-038) |
| Knowledge graph "not located", scored 0/UNKNOWN (§36, #14) | It exists. Rule-based extraction runs on every ingest, `PostgresGraphStore` has owner-scoped tables, and a multi-hop GraphRAG strategy is in `rag/leaves.py`. It hasn't been verified on real Postgres, and the default `simple` strategy never reads it. | GAP-023 `built_not_proven` |
| 7 agent personas | 8 are registered (`aura_core/agents/registry.py:112-201`). Only `engineer` is multi-iteration. | GAP-024 |
| `SYNCABLE_MODELS` "does not exist" (#10) | It exists on the backend (`aura_backend/infrastructure/db/models.py`, imported at `api/v2/sync.py:24`). CLAUDE.md points at the wrong (client) file. | GAP-039 |
| Fine-tuning: "no code path changes weights" | **Still true, with new detail.** e8a960b added a PyTorch/TF "ML service", but `LocalLoraTrainer` reads fields `FineTuneJobSpec` doesn't have. It swallows the `AttributeError` and reports a `modelfile://` success (reproduced). Even when it's reached, the ML service trains a randomly initialised 2-class classifier and never loads the base LLM. | GAP-017, GAP-018 |

## New gaps not in the audit

- **GAP-043 (P0): GitHub Actions isn't running at all.** Jobs die in ~3 s with no steps and no
  runner, on main since 2026-09-22 and on PR #96 (including after a re-run). That's consistent
  with an account-level Actions or billing block. No CI gate runs, and Deploy VPS can't fire.
  Owner: repo owner.
- **GAP-005 (P0): main is still unprotected.** Four direct pushes on 2026-09-23 broke the default
  suite. The fix (`11abb50`) has since merged to main via PR #96, so the breakage is resolved, but
  the process gap that allowed it isn't: branch protection that requires Backend CI is still
  recommended, and it's only meaningful once GAP-043 is fixed.
- **GAP-042 (P1): no model license review is recorded.** The production 3B Qwen2.5 model may be
  under the Qwen Research License (unverified).
- **GAP-009 (P1): `SecretManager`'s "AES-256-CTR" doesn't use the key.** The keystream is
  `sha256(counter)`, so any key decrypts it (reproduced). It's unused today, but it's exported.
- **GAP-008 (P2): ~2.8k LOC of unreviewed packages from 43a2d5c.** None of them has a caller or a
  test, and `continuous_intelligence.__all__` names symbols that don't exist.
- **GAP-018 (P1): training failures are recorded as `SUCCEEDED`.**
- **GAP-027 (P1): the dev stack shares production's single-slot Ollama** over `aura_edge`.
- **GAP-030/031 (P1): model artefacts are unpinned.** That covers the Ollama tag, the HF embedder
  revision and the whisper model.
- **GAP-032 (P2): ten image references are tag-only.**
- **GAP-036 (P1): there is no HTTP request metric,** so the availability, API-latency and 5xx SLOs
  can't be measured.
- **GAP-037 (P2): the turn-latency histogram tops out at 60 s,** while the measured p95 is 70.5 s.
- **GAP-022 (P2): unexplained leak-guard blocks.** `SystemPromptLeakGuard` blocked 3 of 15 turns on
  real model output in A4, and nobody established whether those were true leaks.
- **GAP-034 (P2): the HF Space failover path sends prompts off-host.** It's off by default, but
  production doesn't refuse it.
- **GAP-010 (P2): a committed Windows `aura_hostd.exe` and log files,** plus stray root-level tests.
- **GAP-044 (P2): release APKs are built on a floating Flutter `stable`,** not the 3.44.6 that
  Client CI verifies.
- **GAP-045 (P3): GitHub Actions are referenced by mutable major tags,** including in the deploy
  workflow that holds the VPS SSH key.
- **GAP-040 (P3): the architecture suite fails from a `.claude/worktrees/` checkout** because of
  path-part skipping.

## Totals

<!-- generated from gap_register.yaml -->

| Severity | open | in_progress | built_not_proven | proven | closed_stale |
| --- | --- | --- | --- | --- | --- |
| P0 | 5 | 0 | 0 | 0 | 0 |
| P1 | 12 | 0 | 0 | 0 | 1 |
| P2 | 15 | 0 | 2 | 0 | 1 |
| P3 | 6 | 0 | 1 | 0 | 2 |

## All gaps

Sorted by status (`closed_stale` last), then severity. The full text, citations and `test`/`metric` fields are in the YAML.

| ID | Sev | Status | Component | Owner | Current state (summary) | Exit condition | Depends on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GAP-005 | P0 | `open` | process/ci | repo-admin | The breakage itself is fixed: commits 43a2d5c, e8a960b, 662af14, 53c9106 (plus merge c3aa510) were pushed directly to main on 2026-09-23 and left the default pytest suite unable to build a Container (~200 errors), which also blocks Deploy VPS (workflow_run on… | main protected with required checks, verified via the GitHub API or settings screenshot recorded in docs/reports/, and the first post-protection merge shows gr… | GAP-043 |
| GAP-011 | P0 | `open` | inference/ollama | platform-ops | Production serves one generation at a time: OLLAMA_NUM_PARALLEL "1" (deploy/docker-compose.prod.yml:683), OLLAMA_MAX_LOADED_MODELS "1" (:679), AURA_PROVIDER_LOCAL_MAX_CONCURRENCY 1 (:260), CPU-only. Measured (A4, 2026-08-23, 4-core/15GB sandbox, qwen2.5:3b-in… | Load report in docs/reports/ showing TTFT p95 < 2.5 s and answer p95 < 15 s at 50 concurrent users on the production topology. | GAP-012, GAP-018 |
| GAP-012 | P0 | `open` | inference/gpu-host | platform-ops | No GPU inference host exists. The `vllm` service is behind the `gpu` compose profile (docker-compose.prod.yml:707-735), image vllm/vllm-openai:latest (unpinned), default model Qwen/Qwen2.5-32B-Instruct-AWQ; never run in production. docs/guides/gpu-hybrid-setu… | GPU host serving a pinned model via the gateway, with a report in docs/reports/. | — |
| GAP-041 | P0 | `open` | load-testing/evidence | platform-ops | The only real-model load evidence is A4: max concurrency 8, n=20 sequential, one run, 4-core sandbox (not the VPS, not a GPU). No 1/5/10/25/50/100-user matrix, no soak, no p99. | Report in docs/reports/ covering the full matrix. | GAP-011, GAP-036 |
| GAP-043 | P0 | `open` | process/github-actions | repo-owner | GitHub Actions jobs on this repository die in about 3 seconds with no steps executed and no runner assigned. Seen on main since 2026-09-22 and on PR #96, including after one re-run. This is consistent with an account-level Actions or billing block, not a work… | Account Actions/billing status restored (GitHub Settings -> Billing / Actions), and a linked Backend CI run on main shows every job executing its steps to comp… | — |
| GAP-006 | P1 | `open` | evaluation/ci | ml | .github/workflows/eval-ci.yml is workflow_dispatch-only (lines 3-4), so the agent/real evaluation workflow never gates a merge or a deploy. Deterministic scripted-provider gates DO run inside pytest (tests/e2e/test_docs_corpus_gate.py; agent_tasks suite) -- b… | A PR that deliberately regresses recall is shown blocked by CI (linked run), and a scheduled real-model eval run is linked from docs/reports/. | GAP-021 |
| GAP-009 | P1 | `open` | security/secret-manager | security | aura_core/security/secret_manager.py claims "AES-256-CTR encryption" (line 50) but _aes_ctr_encrypt (lines 133-143) derives its keystream from sha256(counter) only -- the `key` argument is never used. Any ciphertext it produces decrypts with any key (reproduc… | Module removed or fixed, with the wrong-key test failing on the current code. | — |
| GAP-016 | P1 | `open` | data/encryption-at-rest | security | Server-side memory, documents/chunks and v3 personal-OS records are plaintext: aura_memories.content TEXT (aura_core/adapters/postgres_store.py:200-204), aura_documents/aura_chunks content TEXT (:139-187), aura_personal_os_records.fields JSONB (aura_core/adap… | Decision recorded as an ADR; chosen controls implemented and a restore from an encrypted backup drilled. | — |
| GAP-017 | P1 | `open` | learning/fine-tuning | ml | No code path in the default or production configuration changes model weights, and none has been run against a real base model. Production gateway uses ScriptedTrainer (aura_core/container.py:533-557; AURA_FINETUNE_TRAINER unset). The opt-in `training` profil… | A real run report in docs/reports/ with base-model revision, dataset digest, eval delta and approval record. | GAP-012, GAP-018, GAP-006 |
| GAP-018 | P1 | `open` | learning/trainer-failure-reporting | ml | A failed training attempt is recorded as success: LocalLoraTrainer.train falls back to a Modelfile TrainResult on any exception (local_lora_trainer.py:104-118), and FineTuneJobEngine.run marks the job SUCCEEDED with that artefact (aura_core/learning/finetune.… | Test green; blanket except removed. | — |
| GAP-021 | P1 | `open` | evaluation/real-model-quality | ml | All gated quality numbers use scripted providers: groundedness 0.94 / citation coverage 0.83 with ContextGroundedProvider, task completion 100% with EchoProvider (roadmap sec. 5). Retrieval recall@5 0.65 / nDCG 0.51 / MRR 0.37 on a 10-doc corpus with the defa… | Report in docs/reports/ with real-model numbers and conditions. | GAP-012 |
| GAP-027 | P1 | `open` | deploy/dev-prod-inference-sharing | platform-ops | The aura-dev stack uses production's Ollama over the shared aura_edge network (docker-compose.dev.yml:4-5, :142; docker-compose.prod.yml:48-51, ollama networks include aura_edge). With OLLAMA_NUM_PARALLEL=1, any dev traffic queues production turns, and a dev… | Isolation implemented in both compose files in one commit, test green. | GAP-012 |
| GAP-028 | P1 | `open` | deploy/disaster-recovery | platform-ops | Backup service and drill proven once in a disposable sandbox (A2, 2026-08-23, "No VPS or production data was involved", phase-a2-restore-drill.md:17-18). Offsite rclone copy and restore.sh have never been exercised (roadmap A2). No drill on the production VPS… | Drill report from offsite data with measured RPO/RTO in docs/reports/. | GAP-016 |
| GAP-030 | P1 | `open` | supply-chain/model-pinning-llm | platform-ops | The production LLM is referenced by Ollama tag only (qwen2.5:3b-instruct-q4_K_M, docker-compose.prod.yml:256; AURA_MODEL_ALLOWLIST is tag names, deploy/.env.example:187). A re-pushed tag silently changes the model. The Ollama *image* is digest-pinned (:667). | Digest recorded in MODEL_REGISTRY.md and enforced at startup. | — |
| GAP-031 | P1 | `open` | supply-chain/model-pinning-embed-stt | ml | Embedder BAAI/bge-small-en-v1.5 (docker-compose.prod.yml:289) is loaded with SentenceTransformer(model_name) and no revision (aura_core/llm/embeddings.py:358), downloaded from the HF Hub at runtime. faster-whisper `tiny.en` is loaded by size name with no revi… | Revisions recorded in MODEL_REGISTRY.md and enforced by test. | — |
| GAP-036 | P1 | `open` | observability/http-metrics | platform-ops | The metric catalogue (aura_core/observability/metrics.py) has turn/LLM/retrieval/tool/guard metrics but no HTTP request metric (no per-route status/latency histogram). Availability, non-LLM API latency and 5xx rate SLOs are therefore unmeasurable from Prometh… | Metrics live on the VPS and wired into PRODUCTION_SLO alerts. | — |
| GAP-042 | P1 | `open` | supply-chain/model-licensing | coordinator | No model license review is recorded anywhere in the tree. The production chat model is Qwen2.5-3B-Instruct (via the Ollama tag qwen2.5:3b-instruct-q4_K_M); from recall, the 3B size is published under the Qwen Research License rather than the Apache-2.0 of mos… | License review recorded per model in MODEL_REGISTRY.md; any incompatible model replaced. | — |
| GAP-007 | P2 | `open` | admin/testing | admin | aura_admin has 2 Playwright specs (tests/e2e/admin.spec.ts, superadmin.spec.ts) across 9 console areas. admin-ci.yml runs only after "VPS Dev Ops" completes or on manual dispatch (lines 7-17) -- it never runs on a PR touching aura_admin/**, so lint/build brea… | PR-triggered Admin CI green with per-page specs. | GAP-005 |
| GAP-008 | P2 | `open` | backend/unreviewed-packages | backend | 43a2d5c added aura_core/engineering2 (pipeline/models/policy), aura_core/proactive (detector/event/loop/signal), aura_core/continuous_intelligence, and aura_core/security/{policy,authenticator,rate_limiter,secret_manager}.py (~2.8k LOC) without review. Nothin… | Every one of these modules has tests and a caller, or is removed, via reviewed PR. | GAP-005 |
| GAP-010 | P2 | `open` | repo/hygiene | repo-admin | Tracked build artefacts and strays: aura_hostd/build/aura_hostd.exe (a Windows binary built on a developer machine, last changed in 43a2d5c), aura_hostd/build/aura_hostd.log and .log.err, repo-root test_personal_os.py, aura_backend/test_personal_os2.py, repo-… | Files removed via reviewed PR; guard test added. | GAP-005 |
| GAP-013 | P2 | `open` | client/reach | client | Backend surfaces with no Flutter caller (re-verified by grep of aura_client/lib): all /api/v3/learning/* (client /learning route redirects to /feedback, core/routing/router.dart:288), /insights/generate, and /admin/* (most /admin/* is reached by aura_admin in… | Inventory checked in; each gap either wired in the client (Client CI green) or documented as non-client. | — |
| GAP-014 | P2 | `open` | safety/taint | security | Concurrent-batch taint visibility gap, still present and self-documented at aura_core/tools/registry.py:378-389: a step in the same asyncio.gather batch (aura_core/execution/engine.py:208-230, batch of up to settings.tools.max_parallel) as an untrusted_conten… | Test added and green; residual either removed or documented as proven-harmless. | — |
| GAP-015 | P2 | `open` | backend/idempotency | backend | No generic Idempotency-Key mechanism exists anywhere (grep for "idempotency-key" / "idempotency_key" matches only the audit report). Retried POSTs (chat, upload, tool confirmation, sync) rely on natural idempotency where it exists. Two concurrent uploads of t… | Middleware + tests green, verified against real Postgres. | — |
| GAP-019 | P2 | `open` | multimodal/vision-video | ml | No vision or video understanding. Only a capability flag exists (aura_core/domain/llm.py:148 supports_vision, :178-188 needs_vision filter); no adapter, no endpoint, no client path. OCR (Tesseract) extracts text only. aura_core/rag/agentic.py:119 still says "… | Vision turn served end to end with a pinned model and an eval report. | GAP-012 |
| GAP-020 | P2 | `open` | voice/stt-language | ml | STT default is faster-whisper `tiny.en` (aura_core/config/settings.py:1048, :1080), English-only, while TTS ships an Arabic Piper voice (ar_JO-kareem-medium, aura_backend/Dockerfile:54-57). Arabic speech input is not supported by default. Live-voice VAD is en… | WER report in docs/reports/ for both languages on the production ASR model. | GAP-012 |
| GAP-022 | P2 | `open` | safety/system-prompt-leak-guard | security | The first time guards ran on real model output (A4), SystemPromptLeakGuard blocked 3 of 15 concurrency-test turns and GroundednessGuard flagged 9 (phase-a4-load-test.md:103-116). Whether the blocks were true leaks or false positives was never established. | Labelled evaluation report; thresholds tuned or confirmed. | GAP-021 |
| GAP-023 | P2 | `built_not_proven` | knowledge/graph | backend | Audit scored the knowledge graph 0/UNKNOWN ("not located"). It exists: RuleEntityExtractor (aura_core/knowledge/extraction.py) runs on every ingest when KNOWLEDGE_GRAPH_ENABLED (default true, settings.py:397, :443; wired container.py:210-211; engine.py:266-26… | Real-Postgres test green and a strategy comparison report. | GAP-021 |
| GAP-024 | P2 | `open` | agents/autonomy-isolation | backend | 8 personas are registered (aura_core/agents/registry.py:112-201; audit counted 7). Only `engineer` has max_plan_iterations > 1 (=8, :200). Every persona uses memory_scope="shared" (:114-201), so no per-agent memory isolation is exercised. | ADR recording the decision; isolation tests if adopted. | — |
| GAP-025 | P2 | `built_not_proven` | engineer/workspaces | platform-ops | Persistent Engineer workspaces (ADR-013/014, Phase J) are built but not proven: prod compose defaults AURA_SANDBOX_ENABLED false and the sandbox profile is off; no verify-engineer-workspaces report exists in docs/reports/. | The script's report committed under docs/reports/ with all checks passing. | — |
| GAP-029 | P2 | `open` | deploy/single-host | platform-ops | Entire platform (17 services + dev stack) runs on one VPS at /opt/aura-stack; single point of failure (audit R6). Accepted by design until measured need. | Rebuild drill report; availability measured against PRODUCTION_SLO. | GAP-012, GAP-028 |
| GAP-032 | P2 | `open` | supply-chain/container-images | platform-ops | Tag-only (not digest-pinned) images: nginx:1.27-alpine (prod:142), postgres:16-alpine (prod:737, dev:406, deploy/backup/Dockerfile:1), redis:7-alpine (prod:762, dev:430), tecnativa/docker-socket-proxy:0.3.0 (prod:444, dev:239), vimagick/tinyproxy:latest (prod… | Test green in Backend CI. | — |
| GAP-034 | P2 | `open` | inference/remote-failover-policy | security | A rented Hugging Face Space failover provider (`hfspace`) is supported behind AURA_ALLOW_REMOTE_INFERENCE (docker-compose.prod.yml:271-287; docs/guides/ split-inference-deployment.md sec. 8). It is off by default, but when enabled it sends prompts and retriev… | Validation + test, or ADR. | — |
| GAP-037 | P2 | `open` | observability/histogram-buckets | platform-ops | aura_turn_duration_seconds buckets stop at 60 s and aura_llm_ttft_seconds at 12 s (_TURN_BUCKETS / _TTFT_BUCKETS in aura_core/observability/metrics.py). The measured p95 turn latency (70.5 s) lands in +Inf, so histogram_quantile cannot report it. | Buckets extended; Grafana p95 matches load-test p95 within one bucket. | — |
| GAP-044 | P2 | `open` | client/release-toolchain | client | release-apk.yml builds the shipped APK/AAB with subosito/flutter-action `channel: stable` (lines 30-32, 76-80) -- a floating toolchain -- while client-ci.yml verifies with a pinned flutter-version "3.44.6" (lines 36, 68, 102, 142). The binary users install is… | release-apk.yml pinned to the CI Flutter version; a release build run linked. | GAP-043 |
| GAP-026 | P3 | `built_not_proven` | desktop/hostd | client | aura_hostd Units 1-9 in tree with disclosed cuts (real PTY, live xterm, restart-on-crash, bundled binary, interactive consent tiers per roadmap sec. 5); never proven end to end. | End-to-end report in docs/reports/. | GAP-010 |
| GAP-033 | P3 | `open` | supply-chain/dev-tooling | backend | aura_backend/requirements-dev.txt uses ranges (pytest>=8.0, pytest-asyncio>=0.23, pytest-cov>=5.0, testcontainers[postgres]>=4.0.0, pip-tools>=7.4); eval-ci.yml and backend-ci.yml install them unpinned. Runtime deps are fully locked (requirements.lock). | requirements-dev.lock generated and used by CI. | — |
| GAP-035 | P3 | `open` | llm/circuit-breaker | backend | Circuit breaker thresholds are global (AURA_LLM_CIRCUIT_THRESHOLD / _OPEN_S) with no per-provider override (split-inference-deployment.md "The circuit breaker is global"). Heterogeneous vLLM pools will need per-pool tuning. | Setting + test. | GAP-012 |
| GAP-038 | P3 | `open` | backend/legacy-dead-code | backend | Legacy Celery tasks ingest_document_task / ingest_pdf_task (aura_backend/tasks.py:11-57) and their services/agent.py + services/drive.py path are unreachable from the v3 gateway but remain registered on the ingestion worker. | Removed via reviewed PR. | — |
| GAP-039 | P3 | `open` | docs/accuracy | coordinator | CLAUDE.md says SYNCABLE_MODELS lives in aura_client core/database/database.dart; it actually lives in aura_backend/infrastructure/db/models.py (imported by api/v2/sync.py:24). The audit said the symbol "does not exist" -- also wrong. CLAUDE.md still calls aur… | Corrections merged. | — |
| GAP-040 | P3 | `open` | testing/worktree-path-sensitivity | backend | tests/architecture/test_no_external_inference.py:189-192 skips any path containing a `.claude` segment, so when the repo is checked out under .claude/worktrees/ (as agent worktrees are) TestClientDeclaresNoInferencePackage::test_a_pubspec_exists_to_scan fails… | Suite green from a .claude/worktrees checkout. | — |
| GAP-045 | P3 | `open` | supply-chain/ci-actions | repo-admin | Every third-party GitHub Action is referenced by a mutable major tag (actions/checkout@v4, actions/setup-python@v5, docker/build-push-action@v5, subosito/flutter-action@v2, actions/setup-node@v4, actions/upload-artifact@v4, ...), not a commit SHA. The deploy… | All workflows pinned; lint in CI. | — |
| GAP-001 | P1 | `closed_stale` | backend/adapters/postgres | backend | Audit (sec. 12, R2) reported PostgresPool._get_pool() had no asyncio.Lock. No longer true: the lock exists and pool creation uses double-checked locking. | Lock present and regression test green in Backend CI. | — |
| GAP-002 | P2 | `closed_stale` | backend/personal-os | backend | Audit (sec. 21, top-20 #5) said the v3 personal-OS store was create+list only. No longer true: owner-scoped update and delete exist in the Postgres adapter and as tools. | update/delete exist in adapter and tools, with owner- and kind-scoping tests. | — |
| GAP-003 | P3 | `closed_stale` | client/engineering | client | Audit (sec. 22) listed GitHub integration connect/callback/status/disconnect as having zero Flutter callers. No longer true for connect/status/disconnect. | Client calls status/connect/disconnect. | — |
| GAP-004 | P3 | `closed_stale` | backend/ingestion | backend | Audit (sec. 24, top-20 #12) flagged idempotency of legacy ingest_document_task as UNKNOWN. That task has zero callers; the live v3 ingestion path is idempotent on content hash. | Live ingestion path skips unchanged content, with a test. | — |
