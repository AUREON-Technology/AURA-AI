# Provider matrix and inference path — pass B

**Pass:** B — models, providers, inference path
**Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (`ffbbb9f`)
**Date:** 2026-10-31
**Author:** Audit subagent pass B

Companion: `model_inventory.md` (artefact-by-artefact table), `findings_B.json` (sidecar of §5).

## 1. Scope and method

Same file set as `model_inventory.md` §1. Everything here is `CODE-READ` unless labelled otherwise; nothing was executed (`NOT RUNNABLE`: temp drive full, Bash unreliable). Production state is quoted from `docs/status/BACKEND_STATUS.md` (2026-10-28/31) and labelled `DOCUMENTED ONLY`. Railway's environment variables are not in the repository and are `UNKNOWN` except where a status doc or deployment guide names them.

## 2. Provider kinds and host policy

| Kind | Adapter | Builds for | Refused when | Evidence |
| --- | --- | --- | --- | --- |
| `openai_compatible` | `OpenAICompatibleProvider` — `POST /chat/completions`, SSE streaming, tool-call reassembly, usage via `stream_options.include_usage`, Groq `x_groq.usage`, mid-stream `{"error":…}` surfaced, copied-reasoning guard | vLLM, llama.cpp, TGI, LocalAI, Ollama `/v1`, HF Space, hosted open-weight APIs | base URL on `EXTERNAL_INFERENCE_HOSTS` (always); on `HOSTED_OPEN_WEIGHT_HOSTS` without `AURA_ALLOW_HOSTED_INFERENCE`, or with a non-open-weight model, or `is_local=true`, or a `:free` model without `ALLOW_PROMPT_TRAINING`; on `REMOTE_INFERENCE_HOSTS` without `AURA_ALLOW_REMOTE_INFERENCE` | `registry.py:127-140`; `providers/openai_compatible.py`; `settings.py:388-443,487-559`; `external_inference.py:54-75,225-233,282-288` |
| `ollama` | `OllamaProvider` — native `/api/chat`; admin lifecycle `list/pull/delete/create/warm` | Ollama only, when lifecycle ops are wanted | model not in `AURA_MODEL_ALLOWLIST` for pulls (fail-closed, `ollama.py:273-289`) | `registry.py:142-153`; `providers/ollama.py` |
| `echo` | `EchoProvider` | tests | staging/production (`settings.py:1918`) | `registry.py:122-125` |

Fitness: `tests/architecture/test_no_external_inference.py` (six static layers + runtime check, `:7-27`) keeps every list in `external_inference.py` the single source. No hosted endpoint may appear in Python source (ADR-030 §5).

## 3. Provider slot matrix

| Slot / name | Kind | Base URL | Model | `is_local` | `third_party` | Data ceiling | Quality tier | Timeouts (gen / health) | Concurrency | Where configured | Live state (claim) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `local` | openai_compatible | `http://ollama:11434/v1` | `qwen2.5:3b-instruct-q4_K_M` (tag) | true | false | sensitive | 1 | 180 s / 5 s default | `MAX_CONCURRENCY` 1 (gateway, `:434`), 1 (workers, `:782`); Ollama `NUM_PARALLEL=1` (`:952`) | `docker-compose.prod.yml:424-443,774-786` | VPS: UNKNOWN; Railway: absent | IMPLEMENTED — NOT FULLY VERIFIED |
| `large` (optional) | openai_compatible | `${AURA_PROVIDER_LARGE_BASE_URL}` | `${…LARGE_MODEL}` (vLLM `Qwen/Qwen2.5-32B-Instruct-AWQ` default, `:988`) | true (default) | false | sensitive | 3 | 180 s / 5 s | 4 | `:444-454`, service `:980+` (`gpu` profile) | never started | PLANNED ONLY (on any live target) |
| `hfspace` (optional) | openai_compatible | `https://<space>.hf.space/v1` | `${…HFSPACE_MODEL}` (Space default `Qwen/Qwen2.5-7B-Instruct-AWQ`) | false | false (not a hosted-API host; gated by `AURA_ALLOW_REMOTE_INFERENCE`) | sensitive | 2 | 45 s / 30 s | 8 | `:455-479,500`; `control-plane.md:75-91` | Railway: `HTTP 503` (asleep/down) | BROKEN live (claim); code IMPLEMENTED |
| `hosted1..3` | openai_compatible | `${…HOSTEDn_BASE_URL}` (must be on `HOSTED_OPEN_WEIGHT_HOSTS`) | `${…HOSTEDn_MODEL}` (must pass `is_open_weight_model`) | forced `"false"` (`:106,130,154`) | **true** (`settings.py:474-478`) | **internal** by default (`:485`); may be raised, never `sensitive` with `ALLOW_PROMPT_TRAINING` (`:528-537`) | 3 default | 60 s / 10 s | 4 | anchor `x-hosted-providers` `:81-156`, merged into gateway and every worker; key via `/run/secrets/hostedN_api_key`; Railway: plain variables (`control-plane.md:163`) | Railway `hosted1` healthy; `.env.example:197-198` documents Groq `openai/gpt-oss-120b` | IMPLEMENTED — NOT FULLY VERIFIED (Phase 6 unproven) |
| `echo` | echo | — | `echo-1` | false | false | sensitive | 2 | — | 8 | `settings.py:658-661` | refused in prod | test only |

Notes
- `SUPPORTS_JSON_MODE` defaults to `false` on every hosted slot (`:93,117,141`) and to `false` on `local` (settings default `settings.py:262`). A request with `response_schema` therefore excludes those providers (`router.py:330-331`). See B-08.
- `TOKENS_PER_SECOND` defaults to 0 everywhere; a provider that does not declare it never receives a request's `preferred_max_output_tokens` (`domain/llm.py:415-420,502-517`).
- Quota limits (`RPM/RPD/TPM/TPD`) default to 0 = undeclared; only a declared budget refuses before sending (`quota.py:16-20`).

## 4. Inference path (what a generation goes through)

| Stage | Mechanism | Evidence | Verdict |
| --- | --- | --- | --- |
| Construction | `build_providers()` from `AURA_LLM_PROVIDERS` + `AURA_PROVIDER_<NAME>_*`; lazy httpx client on first use | `registry.py:167-176`; `providers/base.py:193-198` | CODE-READ |
| Classification | kernel opens a `data_scope` per turn: baseline INTERNAL, SENSITIVE for Engineer / caller-evidence / image turns; escalated by memory, documents, pinned docs, goals, non-clean session history, any tool output; unscoped generations count as SENSITIVE (fail closed) | `kernel.py:1163-1196`; `tools/registry.py:405`; `sensitivity.py:49-50,86-100` | CODE-READ; tests referenced by status doc, not run here |
| Filter | capability + pool + modality + language + locality + **data classification ceiling** + tools + JSON + quality tier + context window; images never go to a `third_party` provider | `router.py:291-338` | CODE-READ |
| Health / quota exclusion | open circuit excluded; declared-budget refusal or active 429 cooldown excluded at plan time | `router.py:415-422`; `quota.py:258-280` | CODE-READ |
| Ordering | five policies; `BALANCED` default; compose forwards `AURA_LLM_ROUTING_POLICY` (default `balanced`); Railway documented as `quality_first` | `router.py:340-379`; `compose:83`; `control-plane.md:150` | CODE-READ / DOCUMENTED ONLY |
| Admission | per-capacity-group bounded priority queue, per-user quota; **per process** | `admission.py:9-31`; `compose:435-443` (queue 4, timeout 90 s, user cap 2) | CODE-READ |
| Attempt / failover | up to `max_fallbacks` (2) candidates; failover only before the first streamed chunk; 429 benches the provider until its reset without tripping the breaker; non-retryable error stops the loop; no synthetic answer | `router.py:632-755,771-949` | CODE-READ |
| Breaker | consecutive failures (5) → OPEN 30 s → HALF_OPEN single probe; probe slot released on verdict-less exits (F-19 fix) | `circuit.py:32-139`; `router.py:609-618` | CODE-READ (fix in source, not CI-run per `BACKEND_STATUS.md:52`) |
| Audit | every third-party generation written to the audit sink with classification and token counts, never content | `router.py:497-536` | CODE-READ |
| Readiness | `router.health()` probes every provider (`/models`, or `/api/tags` for `ollama`); reported under `/health/ready` | `router.py:985-1010`; `container.py:1752` | CODE-READ |

## 5. Findings

| Local ID | Severity | Title | Subsystem | Evidence (path:line / command) | Current behavior | Expected behavior | Recommended action | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B-01 | P1 | On the live target, every SENSITIVE generation has no eligible provider while `hfspace` is down | llm/routing + deployment | `router.py:325-327` (ceiling filter); `settings.py:485` (hosted ceiling = internal); `sensitivity.py:49-50` (unscoped = SENSITIVE); `kernel.py:1163-1196` and `tools/registry.py:405` (escalations); `BACKEND_STATUS.md:24,35-36` (`hosted1` healthy, `hfspace` 503, no Ollama — DOCUMENTED ONLY); `control-plane.md:149,166` | With providers `hosted1,hfspace` and `hfspace` returning 503, any turn that retrieves memory/documents/goals, has non-clean history, runs a tool, is an Engineer or image turn, plus every background generation (insights, summaries, extraction, automation) is routed only to `hfspace`; after 5 failures its circuit opens and `plan()` returns no candidates → `AllProvidersFailedError` → 503. Only plain, context-free chat works. | A SENSITIVE-capable provider is always available (self-hosted or rented, always-on), or the operator consciously raises the hosted ceiling; readiness distinguishes "sensitive-capable provider down" from "all good". | Bring `hfspace` back on an always-on GPU flavor or add a second rented/self-hosted node; add a readiness/alert field "sensitive_capable_provider_healthy"; document the consequence next to `AURA_LLM_PROVIDERS` in `control-plane.md`. Do not raise `hosted1` to `sensitive` without an owner decision (ADR-030 §4). | High (code); Med (live state is a status-doc claim) |
| B-02 | P1 | Admission and quota limits are per process; the VPS stack runs 4 gateway workers plus Celery workers against a single Ollama slot | llm/admission, deployment | `admission.py:27-31`; `quota.py:36-40`; `compose:403` (`--workers 4`), `:434,:782` (cap 1 each), `:440` (comment admits the multiplier), `:952` (`OLLAMA_NUM_PARALLEL=1`); `gap_register.yaml:286-313` GAP-011 open | Effective concurrency on Ollama is 1 × processes; excess requests queue inside Ollama invisibly until the 180 s timeout. Per-minute quota windows for hosted slots are also per process, so N processes can each spend the provider's RPM. | One shared limiter per capacity group across processes; measured matrix (GAP-041). | Build the Redis-backed admission/quota controller the module docstring names, or run inference-facing processes with `AURA_WORKERS=1`; run the `tests/load` matrix on the real topology. | High (code); Med (impact unmeasured) |
| B-03 | P2 | Embedder, cross-encoder and Whisper weights are loaded by name with no revision; the LLM is an Ollama tag | supply-chain/model pinning | `embeddings.py:358` `SentenceTransformer(self._model_name)`; `reranking.py:104` `CrossEncoder(self._model_name)`; `whisper_stt.py:63` `WhisperModel(self._model_size…)`; `compose:430` tag; contrast `Dockerfile:67-99` (Piper SHA-pinned) | An upstream push to the Hub silently changes embeddings (invalidating the index space ADR-007 guards by model id only), rerank scores and transcripts; the LLM can change under a `docker compose pull`. | Every weight artefact pinned by revision/digest and recorded (`IndexIdentity`, telemetry). | Add `AURA_EMBEDDING_MODEL_REVISION`, `…RERANK_MODEL_REVISION`, `…VOICE_STT_MODEL_REVISION`; pass `revision=` to the loaders; include the revision in `EmbeddingResult.model_id`; pin the Ollama model by digest or serve via vLLM `--revision`. | High |
| B-04 | P2 | HF Space inference image is unpinned (`vllm/vllm-openai:latest`); model revision optional | deployment/inference plane | `deploy/spaces/inference-vllm/Dockerfile:21`; `entrypoint.sh:22-24,65-67`; prod compose pins `vllm/vllm-openai:v0.29.0@sha256:…` (`:984`); prior SEC-07 (`AURA_FULL_SYSTEM_REVIEW.md:251`) | A Space rebuild can change tokenizer/sampling behaviour and API surface; without `VLLM_MODEL_REVISION` the served weights can change. | Image digest-pinned and model revision mandatory for the rented plane, matching the on-host compose. | Pin the Space base image by tag+digest with a deliberate bump policy; make `VLLM_MODEL_REVISION` required in `entrypoint.sh` (fail loudly like the API key). | High |
| B-05 | P2 | No path from a trained LoRA adapter to served traffic; the production trainer trains nothing | learning/fine-tuning | `compose:556` (`scripted`), `:877-905` (`worker-training` profile, not on Railway); `finetune.py:121-140`; `local_lora_trainer.py:16-20,301-312`; `kernel.py:1991-2006` refuses non-tag artefacts; grep: no caller of `OllamaProvider.create_model`/`warm_model` (`admin.py:565-611` only list/pull/delete) | Approval → job → `NOT_TRAINED` on every live target. Even with `worker-training`, a `SUCCEEDED` job registers a CANDIDATE whose artefact the kernel can never serve. | "Learning" produces a servable, evaluated model version or the feature is described as dataset curation only. | Either build the merge→GGUF→`create_model`→canary step (with RegressionGate) or re-label the capability in status docs and the client. | High |
| B-06 | P2 | ADR-030 hosted inference is live on Railway but Phase 6 is unproven, and the only proof tooling targets a VPS whose host key changed | llm/hosted, verification | `verify-hosted-inference.yml:124-134` (needs `deploy/known_hosts`, VPS SSH); `BACKEND_STATUS.md:37` (host key changed, CB-?); `docs/reports/phase-6-hosted-inference-openrouter-inprocess-20261028.txt:1-16,42-43,53` (9/10, 429 skipped, OpenRouter not Groq, echo fallback); `gap_register.yaml:1546-1571` | Production traffic goes to a hosted provider whose 429/cooldown path, Redis-persisted quota and real fallback have never been exercised end to end on any live stack; the workflow cannot run until the VPS key question is settled. | GAP-064 exit condition: a committed report with every check run on a live stack. | Add a Railway-targetable variant of `verify_hosted_inference.py` (it already runs in-process) with Redis and the real fallback, run check 10, commit the report and Groq wire captures. | High |
| B-07 | P3 | Native Ollama adapter reports every completion as `finish_reason=stop` | llm/providers | `providers/ollama.py:170,206`; grep `done_reason` → no match | A `done_reason: "length"` truncation is reported as a clean stop; the kernel and telemetry cannot see truncation. Production uses `openai_compatible` against Ollama `/v1`, so impact is limited to `kind=ollama` deployments. | Map `done_reason` to `FinishReason` as the OpenAI adapter does (`openai_compatible.py:38-45`). | Read `done_reason` in both `complete()` and the final stream event. | High |
| B-08 | P3 | Structured-output requests exclude every default-configured provider | llm/routing + config | `router.py:330-331`; `settings.py:262` (`supports_json_mode` default false); `compose:93,117,141` (hosted slots default false); `.env.example:208` documents the consequence | Any request carrying `response_schema` (ADR-029 structured `/chat`, any engine using JSON mode) gets `no_json_mode` from every provider unless an operator set `SUPPORTS_JSON_MODE=true`; on Railway this is UNKNOWN. | Operators are warned at boot when no enabled provider supports JSON mode while a feature that needs it is on. | Add a boot-time check/warning; set the flag in the documented Groq slot example; expose `excluded` reasons in the admin routing explain. | Med |
| B-09 | P3 | Image understanding is implemented but has no eligible provider on any target | vision | `vision/engine.py:99-106`; `router.py:309-316` (third party refused); `compose:468` (`hfspace` modalities `text`), `:548` (`AURA_VISION_ENABLED` false); `BACKEND_STATUS.md:36` (`hfspace` down) | Image turns fail with `dependency_unavailable` or degrade to OCR text; the client attach UI (`gap_register.yaml:555-557`) ships against a feature that cannot be served. | A pinned VLM pool (inference-plane.md §8) or the feature kept hidden in the client until one exists. | Follow `docs/deployment/inference-plane.md:118-138` on an always-on Space, or gate the client UI on `/health/ready` vision availability. | High (code) / Med (live) |
| B-10 | P3 | Prompt budget is global (400 tokens) and sized for CPU Ollama, so a fast hosted provider is fed the same truncated context | llm/context | `settings.py:639-641,693`; `container.py:242` (one `ContextEngine` for all providers); `compose` does not override `AURA_LLM_MAX_PROMPT_TOKENS` (grep) | RAG/memory context is cut to 400 tokens before routing, regardless of which provider answers; on Railway with an 8k-context hosted model this needlessly limits grounding (unless the dashboard overrides it — UNKNOWN). | Prompt budget derived from the selected provider's context window or configured per pool. | Make `max_prompt_tokens` a ceiling per provider/pool, or at least document the override for hosted-primary topologies in `control-plane.md`. | Med |
| B-11 | P4 | Status and architecture docs disagree with the tree on GAP-018, Ollama-in-production, ADR-030 deployment state and vision | documentation | see §7 | — | — | Update `BACKEND_STATUS.md:69`, `CLAUDE.md` "Deploy" paragraph, ADR-030 status line, `AURA_FULL_SYSTEM_REVIEW.md` §18 pointer. | High |

## 6. Previous-finding recheck

| Prior item | Verdict | Evidence |
| --- | --- | --- |
| GAP-011 single-slot CPU inference (P0) | **still open** | `compose:434,952`; register `open` (`gap_register.yaml:312`); B-02 |
| GAP-012 no GPU host | **still open** | `large` profile never run; `hfspace` 503 (claim) |
| GAP-041 no load matrix | **still open** | register `open` (`:976`); no new report under `docs/reports/` named for it (not searched exhaustively — Glob timed out on the repo root) |
| GAP-034 remote failover policy / D-1 (ADR-017) | **partially fixed / superseded in part** | ADR-030 covers hosted open-weight hosts; rented compute (`hf.space`) is still gated only by `AURA_ALLOW_REMOTE_INFERENCE` (`settings.py:426-443`); no ADR-017 in `docs/architecture/` (brief lists 001–032 without 017) → policy decision still missing |
| CB-16 "rented HF Space as sole provider" | **partially fixed** | docs still show `AURA_LLM_PROVIDERS=hfspace` (`control-plane.md:75-76`, `inference-plane.md:30`); live topology is `hosted1,hfspace` (claim), which swaps "sole remote provider" for "sole SENSITIVE-capable provider" (B-01) |
| CB-19 capacity report | **still open** | as GAP-041 |
| GAP-064 ADR-030 Phase 6 | **still open** (`built_not_proven`) | B-06 |
| F-19 half-open probe leak | **fixed-in-source-not-operationally-verified** | `circuit.py:112-122`; `router.py:609-618,668-670,731-735` |
| F-22 60 s hard-coded request timeout | **fixed-in-source-not-operationally-verified** | `domain/llm.py:546-551` (`timeout_s=None`); `router.py:381-386` |
| SEC-07 HF Space unpinned image, public health/metrics | **still open** | B-04; `/health`/`/metrics` exposure not re-verified here (no Space config read beyond Dockerfile/entrypoint) |
| SEC-06 / GAP-027 dev shares prod Ollama | **cannot verify** | dev compose not read in this pass |
| "Inference concurrency/latency" (brief) | **still open** | B-02; only A4 sandbox numbers exist (`BACKEND_STATUS.md:53`) |
| "Fine-tuning and actual weight updates" (brief) | **still open** | B-05 |
| "Vision/video" (brief) | vision: **IMPLEMENTED BUT UNREACHABLE** (B-09); video: **NOT IMPLEMENTED** (`domain/llm.py:117-122`) | — |
| "Production deployment boundaries" (brief, inference part) | Railway control plane → hosted API (`hosted1`) + rented GPU (`hfspace`); VPS compose → on-host Ollama; HF Space → vLLM. Boundary enforced in code by host categories (`external_inference.py`) and data ceilings; **live wiring UNKNOWN** | `settings.py:388-443`; `BACKEND_STATUS.md:33-38` |

## 7. Documentation inaccuracies

| Doc claim | Code / tree | Verdict |
| --- | --- | --- |
| `docs/status/BACKEND_STATUS.md:69` "GAP-018 (a failed train is recorded as success)" | `learning/finetune.py:447-448,508-533`; `adapters/local_lora_trainer.py:22-27` | STALE (fixed in source) |
| `CLAUDE.md` Deploy section: qwen2.5:3b on Ollama "in production" at 5–10 tok/s | `BACKEND_STATUS.md:24` no Ollama on the live target; `compose:424-434` still the VPS default | STALE for Railway |
| `docs/architecture/030-hosted-open-weight-inference.md:3` "not deployed and not used" | `BACKEND_STATUS.md:24,35`; `control-plane.md:148-158` | STALE |
| `docs/deployment/control-plane.md:75-76`, `inference-plane.md:30` `AURA_LLM_PROVIDERS=hfspace` as the Railway topology | `control-plane.md:148-149` later shows `hosted1,hfspace`; status docs say the latter is live | internally inconsistent; the sole-`hfspace` block should be marked historical |
| `docs/reports/AURA_FULL_SYSTEM_REVIEW.md:327-328` "Vision: not implemented" | `aura_core/vision/engine.py`; `container.py:1111-1124` | superseded |
| `deploy/docker-compose.prod.yml:547` "no vision pool ships yet: GAP-012" | consistent with tree | accurate |
| `docs/production/gap_register.yaml` GAP-012 "vllm `:latest`" | `compose:984` pinned; Space `Dockerfile:21` not | partially stale |

## 8. Verification log

| Step | Tool / target | Result |
| --- | --- | --- |
| Read `aura_core/llm/*.py` (all 15 files listed by Glob) except `cache.py`, `providers/echo.py`, `providers/__init__.py` | Read / Glob | cited above |
| Read `settings.py:244-713` (ProviderConfig, LLMSettings), grep for `validate`, `echo`, `finetune_*` | Read / Grep | `:334` kinds; `:388-559` host gating; `:1918` echo refused in prod |
| Read `external_inference.py` in full | Read | banned / gated / hosted lists; `is_open_weight_model` |
| Read `container.py:160-280,625-735,1080-1210`; grep rerank/finetune wiring | Read / Grep | `:183-210` router wiring; `:281-283` reranker; `:503-532` registry + fine-tune engine |
| Read `kernel.py:1158-1248,1955-2014`; grep `escalate\|data_scope` | Read / Grep | classification baseline + escalations; `_servable_model_tag` |
| Grep `escalate\|SENSITIVE` in `tools/registry.py` | Grep | `:402-405` tool output escalates |
| Grep `data_classification\|hint_for\|workload_scope` in `tasks.py` | Grep | no match → worker generations unclassified (fail closed to SENSITIVE), consistent with `compose:65-67` |
| Grep `router.health()` | Grep | `container.py:1752` |
| Read `deploy/docker-compose.prod.yml:55-83,420-504,866-905,930-989`; grep provider/env keys | Read / Grep | slot anchor, gateway env, training worker, Ollama/vLLM services |
| Read `deploy/spaces/inference-vllm/{Dockerfile,entrypoint.sh}` | Read | B-04 |
| Read `.github/workflows/verify-hosted-inference.yml`, `deploy/scripts/verify-hosted-inference.sh:1-80`, phase-6 in-process report | Read | B-06 |
| Read `aura_backend/railway.json` | Read | health gate is `/health/live`; pre-deploy script |
| Grep `docs/deployment/*.md` for provider vars | Grep | sole-`hfspace` and `hosted1,hfspace` blocks both present |
| Grep `gap_register.yaml` for GAP-011/012/017/018/020/021/027/030/031/034/041/064 | Grep | statuses as cited |
| Glob `tests/**/test_*provider*.py`, `test_*rout*.py`, `tests/fixtures/provider_wire/*` | Glob | `test_provider_wire_contracts.py`, `test_hosted_provider_compose_plumbing.py`, `test_llm_routing.py`, `test_vision_routing.py`, 12 wire fixtures (Groq ×3, OpenRouter ×8, Workers AI ×1) — existence only, not executed |
| `pytest`, `python -c`, any network call | NOT RUNNABLE | temp drive full / Bash unreliable per coordinator; deliberately not attempted |
| Glob on repository root | timed out twice (20 s) | narrowed to subdirectories |
| Railway variables, HF Space settings, VPS containers | UNKNOWN | cannot probe production |
