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
| Idempotency of `ingest_document_task` is UNKNOWN (§24, #12) | That task has no caller — but the **live v3 path was not idempotent either, and leaked across users**: document ids came from the per-process-salted `hash(title)`, not scoped to an owner, so retries duplicated chunks and a second user's same-titled upload became a version of the first user's document (found 2026-09-25, fixed in `9b9f9e5`). | GAP-004 `built_not_proven` (was wrongly `closed_stale`; dead code is GAP-038) |
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

<!-- generated from gap_register.yaml by aura_backend/scripts/render_gap_register.py -->

| Severity | open | in_progress | built_not_proven | proven | closed_stale |
| --- | --- | --- | --- | --- | --- |
| P0 | 4 | 0 | 2 | 1 | 0 |
| P1 | 8 | 1 | 9 | 0 | 1 |
| P2 | 16 | 1 | 11 | 0 | 1 |
| P3 | 6 | 0 | 1 | 0 | 1 |

## All gaps

Sorted by status (`closed_stale` last), then severity. The full text, citations and `test`/`metric` fields are in the YAML; a row's summary is its newest re-verification note when one exists.

| ID | Sev | Status | Component | Owner | Current state (summary) | Exit condition | Depends on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GAP-004 | P0 | `built_not_proven` | backend/ingestion | backend | (2026-09-25) Re-verified by the P1.4 stream; the closed_stale verdict above was wrong. v3 ingestion was NOT idempotent and leaked across users: KnowledgeEngine… | Live ingestion path skips unchanged content, with a test. | — |
| GAP-005 | P0 | `open` | process/ci | repo-admin | The breakage itself is fixed: commits 43a2d5c, e8a960b, 662af14, 53c9106 (plus merge c3aa510) were pushed directly to main on 2026-09-23 and left the default p… | main protected with required checks, verified via the GitHub API or settings screenshot recorded in docs/reports/, and the first post-protection merge shows green required checks. | GAP-043 |
| GAP-011 | P0 | `open` | inference/ollama | platform-ops | (2026-09-25) P2 (15a7dbd) adds admission control in front of every provider: per-provider concurrency caps (now also on stream()), a bounded priority queue (in… | Load report in docs/reports/ showing TTFT p95 < 2.5 s and answer p95 < 15 s at 50 concurrent users on the production topology. | GAP-012, GAP-018 |
| GAP-012 | P0 | `open` | inference/gpu-host | platform-ops | (2026-09-25) deploy/docker-compose.gpu.yml (15a7dbd) defines general, coder and embed vLLM pools on vllm/vllm-openai:v0.29.0 pinned by digest, with a required… | GPU host serving a pinned model via the gateway, with a report in docs/reports/. | — |
| GAP-041 | P0 | `open` | load-testing/evidence | platform-ops | (2026-09-25) The harness now exists (c6e7675): aura_backend/scripts/loadtest (python -m scripts.loadtest), matrix 1..100, scenarios A-F plus a non-LLM API mix,… | Report in docs/reports/ covering the full matrix. | GAP-011, GAP-036 |
| GAP-043 | P0 | `proven` | process/github-actions | repo-owner | (2026-10-11) Runners returned around 2026-10-01: Backend CI on main from run #306 onward executes its steps (3-15 min per run instead of ~4 s). The first green… | Account Actions/billing status restored (GitHub Settings -> Billing / Actions), and a linked Backend CI run on main shows every job executing its steps to completion. | — |
| GAP-048 | P0 | `built_not_proven` | security/regression-suite | security | The P1.5 security regression pass (merged 371bdc1) found and fixed eleven vulnerabilities in the live tree besides the taint gap, each with a regression test t… | Backend CI green on these suites on main, the fixes deployed, and the POST /api/v3/insights/{id}/dismiss owner check (Postgres-only, code-inspected so far) exercised against a real Postgres. | GAP-043, GAP-005 |
| GAP-006 | P1 | `built_not_proven` | evaluation/ci | ml | (2026-09-25) eval-ci.yml was also invalid YAML (unquoted sqlite+aiosqlite:///:memory: at line 45), so GitHub recorded a failed run on every push and it had nev… | A PR that deliberately regresses recall is shown blocked by CI (linked run), and a scheduled real-model eval run is linked from docs/reports/. | GAP-021 |
| GAP-009 | P1 | `built_not_proven` | security/secret-manager | security | (2026-09-25) Fixed in b16d168: AES-256-GCM (cryptography AESGCM, imported lazily) with the secret id as associated data; rotate_key rewritten (it also called t… | Module removed or fixed, with the wrong-key test failing on the current code. | — |
| GAP-016 | P1 | `built_not_proven` | data/encryption-at-rest | security | (2026-09-25) Column encryption built in f11b543: aura_memories.content and aura_personal_os_records.fields sealed with Fernet (envelope aura-enc:v1:<key id>:..… | Decision recorded as an ADR; chosen controls implemented and a restore from an encrypted backup drilled. | — |
| GAP-017 | P1 | `built_not_proven` | learning/fine-tuning | ml | (2026-09-25-w2) Wave 2 (d99dcbc, f46a7bd): LocalLoraTrainer now runs real PEFT LoRA against the job's base model (transformers/PEFT imported lazily, only in th… | A real run report in docs/reports/ with base-model revision, dataset digest, eval delta and approval record. | GAP-012, GAP-018, GAP-006 |
| GAP-018 | P1 | `built_not_proven` | learning/trainer-failure-reporting | ml | (2026-09-25-w2) Fixed in wave 2 (58063ee, 09eefb8): the job engine records a trainer result that did not train as NOT_TRAINED with its reason, never SUCCEEDED;… | Test green; blanket except removed. | — |
| GAP-021 | P1 | `open` | evaluation/real-model-quality | ml | All gated quality numbers use scripted providers: groundedness 0.94 / citation coverage 0.83 with ContextGroundedProvider, task completion 100% with EchoProvid… | Report in docs/reports/ with real-model numbers and conditions. | GAP-012 |
| GAP-027 | P1 | `open` | deploy/dev-prod-inference-sharing | platform-ops | The aura-dev stack uses production's Ollama over the shared aura_edge network (docker-compose.dev.yml:4-5, :142; docker-compose.prod.yml:48-51, ollama networks… | Isolation implemented in both compose files in one commit, test green. | GAP-012 |
| GAP-028 | P1 | `open` | deploy/disaster-recovery | platform-ops | (2026-09-25) 3d9a318 adds age-encrypted backup sets with an encrypted plaintext-hash manifest and a keyless-checkable CHECKSUMS.sha256, GFS retention that neve… | Drill report from offsite data with measured RPO/RTO in docs/reports/. | GAP-016 |
| GAP-030 | P1 | `open` | supply-chain/model-pinning-llm | platform-ops | The production LLM is referenced by Ollama tag only (qwen2.5:3b-instruct-q4_K_M, docker-compose.prod.yml:256; AURA_MODEL_ALLOWLIST is tag names, deploy/.env.ex… | Digest recorded in MODEL_REGISTRY.md and enforced at startup. | — |
| GAP-031 | P1 | `open` | supply-chain/model-pinning-embed-stt | ml | Embedder BAAI/bge-small-en-v1.5 (docker-compose.prod.yml:289) is loaded with SentenceTransformer(model_name) and no revision (aura_core/llm/embeddings.py:358),… | Revisions recorded in MODEL_REGISTRY.md and enforced by test. | — |
| GAP-036 | P1 | `open` | observability/http-metrics | platform-ops | (2026-09-25) The load harness confirmed two more measurement holes: aura_turns_in_flight is only updated on the non-streaming AIKernel.run path (every client u… | Metrics live on the VPS and wired into PRODUCTION_SLO alerts. | — |
| GAP-042 | P1 | `open` | supply-chain/model-licensing | coordinator | No model license review is recorded anywhere in the tree. The production chat model is Qwen2.5-3B-Instruct (via the Ollama tag qwen2.5:3b-instruct-q4_K_M); fro… | License review recorded per model in MODEL_REGISTRY.md; any incompatible model replaced. | — |
| GAP-046 | P1 | `open` | deploy/backup-secret-scope | platform-ops | The backup service mounts the whole deploy/secrets directory read-only at /run/backup-secrets (docker-compose.prod.yml, since 681780d), in a container on the e… | Backup-only secrets moved to their own directory or individual file mounts, with the fitness test, and one backup cycle run on the VPS after the change. | GAP-028 |
| GAP-050 | P1 | `built_not_proven` | adaptation/connectors | platform-core | (2026-10-11) AP2's tests ran green in Backend CI run #347 and on PostgreSQL 16.15 (2026-10-11), but its exit criterion also needs the dev-stack run, and the de… | AP2 exit criteria met; docs/reports/ap2-connector-framework.md. | GAP-049, GAP-048 |
| GAP-054 | P1 | `built_not_proven` | adaptation/compiler | platform-core | (2026-10-11) AP6 exit criteria (both pilots compile schema-valid blueprints; fitness and fuzz tests green in CI) are met. Backend CI run #347 on main ran the A… | AP6 exit criteria met; docs/reports/ap6-blueprint-compiler.md. | GAP-053, GAP-055 |
| GAP-056 | P1 | `built_not_proven` | adaptation/runtime | platform-core | (2026-10-11) AP7's tests ran green in Backend CI run #347 and on PostgreSQL 16.15 (2026-10-11); deploy and rollback on the dev stack are still unrecorded (the… | AP7 exit criteria met; docs/reports/ap7-blueprint-runtime.md. | GAP-054, GAP-048 |
| GAP-059 | P1 | `in_progress` | adaptation/knowledge-and-identity | platform-core | Files (tables and documents) and Git (checkout) connectors built; CompositeFetcher wired with connector-backed refresh sources; CB-11 fixed. Still no MySQL/SQL… | AP10 exit criteria met after CB-11 is fixed; docs/reports/ap10-connectors-knowledge.md. | GAP-058 |
| GAP-063 | P1 | `built_not_proven` | integration/flame-readiness | platform-core | AURA-side requirements AR-1..AR-8 for Flame in Freefall (FCDS-College/Nasa, docs/AURA_INTEGRATION_REQUIREMENTS.md) are built and unit/security tested in this t… | docs/reports/flame-link2-live.md (AR-7: a deployed bp<N>_api_search answering a real question with a Flame evidence_id) and docs/reports/flame-link1-live.md both exist. | GAP-056, GAP-057, GAP-061 |
| GAP-007 | P2 | `open` | admin/testing | admin | (2026-09-25-w2) Wave 2 did not add admin Playwright specs (the Flutter/admin stream was interrupted before reaching the admin console). node 22 and npm are ava… | PR-triggered Admin CI green with per-page specs. | GAP-005 |
| GAP-008 | P2 | `open` | backend/unreviewed-packages | backend | 43a2d5c added aura_core/engineering2 (pipeline/models/policy), aura_core/proactive (detector/event/loop/signal), aura_core/continuous_intelligence, and aura_co… | Every one of these modules has tests and a caller, or is removed, via reviewed PR. | GAP-005 |
| GAP-010 | P2 | `open` | repo/hygiene | repo-admin | Tracked build artefacts and strays: aura_hostd/build/aura_hostd.exe (a Windows binary built on a developer machine, last changed in 43a2d5c), aura_hostd/build/… | Files removed via reviewed PR; guard test added. | GAP-005 |
| GAP-013 | P2 | `in_progress` | client/reach | client | (2026-09-25-w2) Wave 2: docs/guides/api-reachability.md is now generated by aura_backend/scripts/api_reachability.py and gated by tests/architecture/test_api_r… | Inventory checked in; each gap either wired in the client (Client CI green) or documented as non-client. | — |
| GAP-014 | P2 | `built_not_proven` | safety/taint | security | (2026-09-25) Fixed in the P1.5 merge (371bdc1): ExecutionEngine._taint_ordered and ToolRegistry.execute_many defer any side-effecting, non-workspace_local call… | Test added and green; residual either removed or documented as proven-harmless. | — |
| GAP-015 | P2 | `built_not_proven` | backend/idempotency | backend | (2026-09-25) Built in 9b9f9e5: IdempotencyStore port, in-memory and Postgres adapters, Alembic 0006, opt-in route dependency on 11 v3 and 6 admin side-effectin… | Middleware + tests green, verified against real Postgres. | — |
| GAP-019 | P2 | `open` | multimodal/vision-video | ml | No vision or video understanding. Only a capability flag exists (aura_core/domain/llm.py:148 supports_vision, :178-188 needs_vision filter); no adapter, no end… | Vision turn served end to end with a pinned model and an eval report. | GAP-012 |
| GAP-020 | P2 | `open` | voice/stt-language | ml | STT default is faster-whisper `tiny.en` (aura_core/config/settings.py:1048, :1080), English-only, while TTS ships an Arabic Piper voice (ar_JO-kareem-medium, a… | WER report in docs/reports/ for both languages on the production ASR model. | GAP-012 |
| GAP-022 | P2 | `open` | safety/system-prompt-leak-guard | security | The first time guards ran on real model output (A4), SystemPromptLeakGuard blocked 3 of 15 concurrency-test turns and GroundednessGuard flagged 9 (phase-a4-loa… | Labelled evaluation report; thresholds tuned or confirmed. | GAP-021 |
| GAP-023 | P2 | `built_not_proven` | knowledge/graph | backend | (2026-09-25-w2) Wave 2 (7768855..99abeab): owner-scoped graph schema with aliases, type vocabulary, per- source provenance, recomputed confidence and valid_fro… | Real-Postgres test green and a strategy comparison report. | GAP-021 |
| GAP-024 | P2 | `built_not_proven` | agents/autonomy-isolation | backend | (2026-09-25-w2) Wave 2 (ADR-016): every persona runs one bounded loop (execution/agentic_loop.py) with max_iterations, token, time, tool and risk budgets as Ag… | ADR recording the decision; isolation tests if adopted. | — |
| GAP-025 | P2 | `built_not_proven` | engineer/workspaces | platform-ops | Persistent Engineer workspaces (ADR-013/014, Phase J) are built but not proven: prod compose defaults AURA_SANDBOX_ENABLED false and the sandbox profile is off… | The script's report committed under docs/reports/ with all checks passing. | — |
| GAP-029 | P2 | `open` | deploy/single-host | platform-ops | Entire platform (17 services + dev stack) runs on one VPS at /opt/aura-stack; single point of failure (audit R6). Accepted by design until measured need. | Rebuild drill report; availability measured against PRODUCTION_SLO. | GAP-012, GAP-028 |
| GAP-032 | P2 | `open` | supply-chain/container-images | platform-ops | Tag-only (not digest-pinned) images: nginx:1.27-alpine (prod:142), postgres:16-alpine (prod:737, dev:406, deploy/backup/Dockerfile:1), redis:7-alpine (prod:762… | Test green in Backend CI. | — |
| GAP-034 | P2 | `open` | inference/remote-failover-policy | security | (2026-09-25) PR #97 (docs/deployment/inference-plane.md) makes the rented HF Space a supported *primary* provider for the Railway target, with no Ollama fallba… | Validation + test, or ADR. | — |
| GAP-037 | P2 | `open` | observability/histogram-buckets | platform-ops | aura_turn_duration_seconds buckets stop at 60 s and aura_llm_ttft_seconds at 12 s (_TURN_BUCKETS / _TTFT_BUCKETS in aura_core/observability/metrics.py). The me… | Buckets extended; Grafana p95 matches load-test p95 within one bucket. | — |
| GAP-044 | P2 | `open` | client/release-toolchain | client | release-apk.yml builds the shipped APK/AAB with subosito/flutter-action `channel: stable` (lines 30-32, 76-80) -- a floating toolchain -- while client-ci.yml v… | release-apk.yml pinned to the CI Flutter version; a release build run linked. | GAP-043 |
| GAP-047 | P2 | `open` | data/dev-prod-key-sharing | platform-ops | aura (prod) and aura-dev on the shared VPS both read deploy/secrets, so they share one data_encryption_keys: dev can decrypt production memory/personal-OS ciph… | Dev stack uses its own key file; documented in deployment-guide.md. | GAP-016 |
| GAP-049 | P2 | `built_not_proven` | adaptation/domain-model | platform-core | (2026-10-11) AP1 exit criteria (schema published; tests green in CI on main) are met. Backend CI run #347 on main ran the AP1 tests green (aura_core/adaptation… | AP1 exit criteria met with CI evidence in docs/reports/ap1-domain-model.md. | GAP-043 |
| GAP-051 | P2 | `built_not_proven` | adaptation/discovery | platform-core | (2026-10-11) AP3 exit criteria (goldens green in CI; live Postgres run for both pilots recorded (ap3 report)) are met. Backend CI run #347 on main ran the AP3… | AP3 exit criteria met; docs/reports/ap3-discovery.md. | GAP-050 |
| GAP-052 | P2 | `built_not_proven` | adaptation/project-twin | platform-core | (2026-10-11) AP4 exit criteria (twins on CPU-only CI; diff goldens green; one twin persisted and diffed on real Postgres (ap4 report)) are met. Backend CI run… | AP4 exit criteria met; docs/reports/ap4-project-twin.md. | GAP-051 |
| GAP-053 | P2 | `built_not_proven` | adaptation/capabilities | platform-core | (2026-10-11) AP5 exit criteria (both pilots planned from the same primitive set; cross-domain report) are met. Backend CI run #347 on main ran the AP5 tests gr… | AP5 exit criteria met; docs/reports/ap5-capability-planning.md. | GAP-052 |
| GAP-055 | P2 | `built_not_proven` | tools/output-schema | platform-core | (2026-10-11) The field and its validation ran green in Backend CI run #347. The target's row-aware truncation was missing until 2026-10-11 (results were cut mi… | Field and validation merged with tests green in CI (part of AP6). | — |
| GAP-057 | P2 | `built_not_proven` | adaptation/evaluation-gate | evaluation | (2026-10-11) The gate's tests ran green in Backend CI run #347. Fixes on 2026-10-11: harvested identifiers are scoped per connector, the status oracle uses the… | AP8 exit criteria met; docs/reports/ap8-generated-eval-gate.md. | GAP-056, GAP-021 |
| GAP-058 | P2 | `open` | admin/project-factory | admin-console | The console has 9 fixed ops areas and no project, connector, twin or blueprint views. | AP9 exit criteria met; docs/reports/ap9-console-mvp.md. | GAP-056, GAP-057, GAP-007 |
| GAP-060 | P2 | `open` | adaptation/drift | platform-core | No re-discovery, drift detection or impact analysis; one TriggerSpec exists; no workflow runtime. | AP11 exit criteria met; docs/reports/ap11-continuous-adaptation.md. | GAP-059 |
| GAP-061 | P2 | `open` | adaptation/surfaces-and-writes | platform-core | No embeddable widget or public AI API; Flutter routes are hardcoded; no write path to external systems. | AP12a/b/c reports in docs/reports/; AP12c only after SEC-01 is closed. | GAP-060, GAP-013 |
| GAP-062 | P2 | `open` | adaptation/project-zero | platform-core | Personal AURA's personas and domains are hardcoded (agents/registry.py, tools/records.py, Life Hub panes) and run outside any blueprint. | AP13.1–AP13.5 exit criteria met; docs/reports/ap13-project-zero.md. | GAP-056 |
| GAP-026 | P3 | `built_not_proven` | desktop/hostd | client | aura_hostd Units 1-9 in tree with disclosed cuts (real PTY, live xterm, restart-on-crash, bundled binary, interactive consent tiers per roadmap sec. 5); never… | End-to-end report in docs/reports/. | GAP-010 |
| GAP-033 | P3 | `open` | supply-chain/dev-tooling | backend | aura_backend/requirements-dev.txt uses ranges (pytest>=8.0, pytest-asyncio>=0.23, pytest-cov>=5.0, testcontainers[postgres]>=4.0.0, pip-tools>=7.4); eval-ci.ym… | requirements-dev.lock generated and used by CI. | — |
| GAP-035 | P3 | `open` | llm/circuit-breaker | backend | Circuit breaker thresholds are global (AURA_LLM_CIRCUIT_THRESHOLD / _OPEN_S) with no per-provider override (split-inference-deployment.md "The circuit breaker… | Setting + test. | GAP-012 |
| GAP-038 | P3 | `open` | backend/legacy-dead-code | backend | Legacy Celery tasks ingest_document_task / ingest_pdf_task (aura_backend/tasks.py:11-57) and their services/agent.py + services/drive.py path are unreachable f… | Removed via reviewed PR. | — |
| GAP-039 | P3 | `open` | docs/accuracy | coordinator | CLAUDE.md says SYNCABLE_MODELS lives in aura_client core/database/database.dart; it actually lives in aura_backend/infrastructure/db/models.py (imported by api… | Corrections merged. | — |
| GAP-040 | P3 | `open` | testing/worktree-path-sensitivity | backend | tests/architecture/test_no_external_inference.py:189-192 skips any path containing a `.claude` segment, so when the repo is checked out under .claude/worktrees… | Suite green from a .claude/worktrees checkout. | — |
| GAP-045 | P3 | `open` | supply-chain/ci-actions | repo-admin | Every third-party GitHub Action is referenced by a mutable major tag (actions/checkout@v4, actions/setup-python@v5, docker/build-push-action@v5, subosito/flutt… | All workflows pinned; lint in CI. | — |
| GAP-001 | P1 | `closed_stale` | backend/adapters/postgres | backend | Audit (sec. 12, R2) reported PostgresPool._get_pool() had no asyncio.Lock. No longer true: the lock exists and pool creation uses double-checked locking. | Lock present and regression test green in Backend CI. | — |
| GAP-002 | P2 | `closed_stale` | backend/personal-os | backend | Audit (sec. 21, top-20 #5) said the v3 personal-OS store was create+list only. No longer true: owner-scoped update and delete exist in the Postgres adapter and… | update/delete exist in adapter and tools, with owner- and kind-scoping tests. | — |
| GAP-003 | P3 | `closed_stale` | client/engineering | client | Audit (sec. 22) listed GitHub integration connect/callback/status/disconnect as having zero Flutter callers. No longer true for connect/status/disconnect. | Client calls status/connect/disconnect. | — |
