# Model inventory — pass B (models, providers, inference)

**Pass:** B — models, providers, inference path, speech/vision/OCR engines, fine-tuning
**Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (`ffbbb9f`)
**Date:** 2026-10-31
**Author:** Audit subagent pass B

Companion documents: `provider_matrix.md` (provider/slot matrix, routing, full Findings table) and `findings_B.json` (sidecar). Findings are numbered `B-01…` once, in `provider_matrix.md`; this file repeats only the rows that are model-specific.

## 1. Scope and method

Read (CODE-READ unless stated): `aura_core/llm/{registry,router,admission,quota,circuit,sensitivity,workload,embeddings}.py`, `aura_core/llm/providers/{base,openai_compatible,ollama}.py`, `aura_core/domain/llm.py`, `aura_core/config/{settings,external_inference}.py`, `aura_core/container.py` (provider, embedder, OCR, vision, STT, TTS, fine-tune wiring), `aura_core/knowledge/reranking.py`, `aura_core/vision/engine.py`, `aura_core/adapters/{whisper_stt,piper_tts,local_lora_trainer}.py`, `aura_core/learning/finetune.py`, `aura_core/kernel/kernel.py` (classification and model assignment), `aura_core/api/admin.py` (model lifecycle routes), `deploy/docker-compose.prod.yml`, `deploy/spaces/inference-vllm/{Dockerfile,entrypoint.sh}`, `aura_backend/Dockerfile`, `aura_backend/requirements.lock`, `aura_backend/railway.json`, `.github/workflows/verify-hosted-inference.yml`, `deploy/scripts/verify-hosted-inference.sh`, ADR-030, `docs/status/BACKEND_STATUS.md`, `docs/production/gap_register.yaml`, `docs/reports/AURA_FULL_SYSTEM_REVIEW.md` §16–19, `docs/deployment/*.md` (grep).

Not run: no `pytest`, no `python -c`, no network probe. The coordinator reported the C: drive (temp) full and Bash output unreliable, so every observation below is a file read, not an execution (`NOT RUNNABLE` in the log). Railway's live environment variables cannot be read from the repository; every "live" statement is a claim from `docs/status/BACKEND_STATUS.md` (2026-10-28/31) and is labelled `DOCUMENTED ONLY`.

## 2. Model inventory

Columns: *Where it is declared* is the code/config that names the artefact; *Pinned* says whether an exact revision/digest is fixed; *Deployment* is what the repository says about each target (VPS compose, Railway, HF Space).

| # | Role | Artefact | Runtime / adapter | Where declared | Pinned | Deployment (repo evidence) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | Chat LLM (VPS default) | `qwen2.5:3b-instruct-q4_K_M` | Ollama, reached through its OpenAI `/v1` surface by `OpenAICompatibleProvider` (`kind=openai_compatible`) | `deploy/docker-compose.prod.yml:428-434` (gateway), `:776-782` (workers); Ollama service `:934-962` (`OLLAMA_NUM_PARALLEL "1"`, `MAX_LOADED_MODELS "1"`, 6 CPU / 10 GB) | **No** — Ollama tag only; digest-pinned image `ollama/ollama:latest@sha256:9d3090…` (`:936`) but not the weights (GAP-030) | VPS compose only. Not on Railway (`docs/status/BACKEND_STATUS.md:24`: "no Ollama", DOCUMENTED ONLY). VPS state UNKNOWN (deploy path dead since 2026-10-18) | IMPLEMENTED — NOT FULLY VERIFIED (A4 sandbox numbers only, `BACKEND_STATUS.md:53`) |
| M2 | Chat LLM, hosted open-weight slot `hosted1` | Live value UNKNOWN from the repo. `deploy/.env.example:197-198` documents Groq `https://api.groq.com/openai/v1` / `openai/gpt-oss-120b`; status docs and the owner's notes say Railway runs that (DOCUMENTED ONLY) | `OpenAICompatibleProvider`, third-party, data ceiling `internal` by default (`settings.py:485`) | compose anchor `x-hosted-providers` `:81-156`; Railway: dashboard variables (not in git) | n/a (vendor-served) | Railway: `hosted1` healthy per `BACKEND_STATUS.md:24,35` | IMPLEMENTED — NOT FULLY VERIFIED (ADR-030 Phase 6 unproven, GAP-064) |
| M3 | Chat LLM, rented GPU `hfspace` | vLLM on a Hugging Face GPU Space; default `Qwen/Qwen2.5-7B-Instruct-AWQ` (`deploy/spaces/inference-vllm/entrypoint.sh:18`); `VLLM_MODEL_REVISION` optional (`:22-24`) | `OpenAICompatibleProvider`, `is_local=false`, gated by `AURA_ALLOW_REMOTE_INFERENCE` (`settings.py:426-443`) | compose `:455-479,500`; `docs/deployment/control-plane.md:75-91` | **Weights: optional**; **image: `FROM vllm/vllm-openai:latest`** unpinned (`Dockerfile:21`) | Railway: `HTTP 503` on 2026-10-28 (`BACKEND_STATUS.md:24,36`) | BROKEN / asleep on the live target (claim); code IMPLEMENTED |
| M4 | Chat LLM, optional on-host GPU `large` | `Qwen/Qwen2.5-32B-Instruct-AWQ` default (`docker-compose.prod.yml:988`) on `vllm/vllm-openai:v0.29.0@sha256:c2914…` (`:984`) behind the `gpu` compose profile (`:980-981`) | `OpenAICompatibleProvider`, `is_local=true` default (`:454`) | compose `:444-454`, `:967-989` | Image yes; model by HF id, revision not set in the default command | Never started (`AURA_FULL_SYSTEM_REVIEW.md:305`, DOCUMENTED ONLY) | NOT DEPLOYED (PLANNED ONLY on any live target) |
| M5 | Chat LLM, test | `echo-1` (`EchoProvider`) | `kind=echo`, declares `text,image` | `settings.py:658-661` | — | Refused in staging/production by `AuraSettings.validate()` (`settings.py:1918`) | Test substrate only |
| M6 | Embeddings (prod) | `BAAI/bge-small-en-v1.5`, 384-d | `SentenceTransformerProvider`, in-process, lazy, `asyncio.to_thread` (`embeddings.py:290-373`) | compose `:480-482`; `container.py:698-704` | **No** — `SentenceTransformer(self._model_name)` with no `revision` (`embeddings.py:358`) (GAP-031) | VPS compose default; Railway: UNKNOWN (Dockerfile installs `sentence-transformers==6.0.0`, `torch==2.13.0+cpu`, `requirements.lock:2188,2314`) | IMPLEMENTED — NOT FULLY VERIFIED |
| M7 | Embeddings (dev/test) | `hash-embed-v1` (`HashEmbeddingProvider`, character-shingle hashing, not semantic) | in-process | `settings.py:626-628` default; `container.py:696-697` | deterministic | Docstring says `validate()` refuses it in staging/production (`embeddings.py:70-72`) — CODE-PRESENT / BEHAVIOR UNVERIFIED (the refusal line itself was not located in this pass) | Test substrate |
| M8 | Embeddings (remote option) | any `/v1/embeddings` server named by `AURA_EMBEDDING_PROVIDER=<provider name>` | `OpenAICompatibleEmbeddingProvider` (`embeddings.py:145-287`), batched, order-preserving, dimension-checked | `container.py:705-719` | depends on server | not configured in any compose file | IMPLEMENTED — unused |
| M9 | Reranker (default) | heuristic lexical reranker | `HeuristicReranker` | `settings.py:724` (`rerank_provider: "heuristic"`) | — | default everywhere | IMPLEMENTED |
| M10 | Reranker (opt-in) | `cross-encoder/ms-marco-MiniLM-L-6-v2` | `CrossEncoderReranker`, in-process, degrades to heuristic on any error (`reranking.py:21-24,73-113`) | `settings.py:725`; `container.py:281-283` | **No** — `CrossEncoder(self._model_name)` no revision (`reranking.py:104`) | opt-in via `AURA_KNOWLEDGE_RERANK_PROVIDER`; not set in prod compose (grep) | IMPLEMENTED — not deployed |
| M11 | Speech-to-text | faster-whisper `tiny.en`, CPU, int8 | `FasterWhisperEngine`, lazy load off-loop (`whisper_stt.py:36-68`) | `settings.py:1490-1491`; `container.py:1126-1136` | **No** — `WhisperModel(model_size)` pulls from the Hub by name (`whisper_stt.py:33,63`); `faster-whisper==1.2.1`, `ctranslate2==4.8.1` pinned in the lock (`requirements.lock:593,546`) | Baked dependency in the gateway image; English-only while Arabic TTS ships (GAP-020) | IMPLEMENTED — NOT FULLY VERIFIED (F2 round trip only) |
| M12 | Text-to-speech | Piper `en_US-lessac-medium.onnx`, `ar_JO-kareem-medium.onnx` (+ `libtashkeel_model.ort`); espeak-ng last resort | `PiperTtsEngine` shelling out to the `piper` binary; `LanguageAwareTtsEngine` wrapper (`container.py:1138-1196`) | `settings.py:1500-1506`; `aura_backend/Dockerfile:63-106` | **Yes** — binary and both voices SHA256-verified at build (`Dockerfile:67-99`) | baked into the gateway image | IMPLEMENTED — NOT FULLY VERIFIED |
| M13 | OCR | Tesseract, `eng` | `TesseractOcrEngine` via `pytesseract==0.3.13` (`requirements.lock:1702`); built only when the binary is on PATH (`container.py:1085-1095`) | `settings.py:753-754`; `Dockerfile:79` | apt package, unpinned version | baked into the gateway image | IMPLEMENTED (prior review: proven end to end, `AURA_FULL_SYSTEM_REVIEW.md:327`) |
| M14 | Vision (image understanding, ADR-031) | no model shipped. Needs any provider declaring the `image` modality that is **not** `third_party` (`vision/engine.py:99-106`; `router.py:309-316`); OCR fallback otherwise | `VisionEngine` (perception step → DATA-channel evidence) | `container.py:1111-1124`; `AURA_VISION_ENABLED` default `false` (`settings.py:1611`; compose `:548`) | — | No image-capable provider on any target: `hfspace` modalities default `text` (compose `:468`), `hosted1` is third party and is refused images regardless | IMPLEMENTED BUT UNREACHABLE |
| M15 | Video | none | — | `Modality` enum has only `text`, `image`, `audio` (`domain/llm.py:117-122`) | — | — | NOT IMPLEMENTED |
| M16 | Fine-tuned model `aura-ft` | PEFT LoRA adapter directory (`adapter_model.safetensors`), base must be a HF repo id or local checkpoint — an Ollama tag is refused (`local_lora_trainer.py:120-128`) | `LocalLoraTrainer` (`AURA_FINETUNE_TRAINER=local`, `worker-training` image only, `compose:877-905`); gateway/workers use `ScriptedTrainer` which trains nothing (`compose:556`; `finetune.py:121-140`) | `container.py:645-677`, `:503-532` | adapter sha256 + resolved base revision written to `training_manifest.json` (`local_lora_trainer.py:265-295`) | Not merged, not converted, not servable: the kernel refuses a directory/URI artefact as a model tag (`kernel.py:1991-2006`); no caller of `OllamaProvider.create_model` exists outside the adapter (grep: admin routes use `list/pull/delete` only, `admin.py:565-611`) | PARTIALLY IMPLEMENTED (GAP-017 `built_not_proven` is accurate) |

### 2.1 What runs where (repository evidence only)

| Target | LLM providers | Embedder | STT/TTS/OCR | Vision | Trainer | Evidence label |
| --- | --- | --- | --- | --- | --- | --- |
| Contabo VPS (`docker-compose.prod.yml`) | `local` (M1); optional `large` (M4), `hfspace` (M3), `hosted1..3` (M2) — all inert unless listed in `AURA_LLM_PROVIDERS` | M6 (bge-small) | M11/M12/M13 baked | off | `scripted` on gateway/workers; `local` only on `worker-training` (profile) | CODE-READ; live state UNKNOWN (deploy dead since 2026-10-18) |
| Railway control plane | `hosted1` + `hfspace` (claim: `BACKEND_STATUS.md:24,35`; `control-plane.md:148-158` pattern) | UNKNOWN (same image; env not in git) | same image | UNKNOWN (default off) | `scripted` (no training worker exists on Railway) | DOCUMENTED ONLY |
| HF Space | vLLM M3 | — | — | text-only unless `VLLM_MAX_IMAGES_PER_PROMPT>0` (`entrypoint.sh:25-28,68-80`) | — | CODE-READ; 503 live (claim) |

## 3. Model-specific findings (subset of `provider_matrix.md` §Findings)

| Local ID | Severity | Title | Pointer |
| --- | --- | --- | --- |
| B-03 | P2 | Embedder, cross-encoder and Whisper weights are loaded by name with no revision; the LLM is an Ollama tag | `provider_matrix.md` B-03 |
| B-04 | P2 | HF Space inference image is `vllm/vllm-openai:latest`; model revision optional | B-04 |
| B-05 | P2 | No path from a trained LoRA adapter to served traffic; production trainer trains nothing | B-05 |
| B-07 | P3 | Native Ollama adapter reports every completion as `finish_reason=stop` | B-07 |
| B-10 | P3 | Image understanding is implemented but has no eligible provider on any target | B-10 |
| B-11 | P3 | Prompt budget (`max_prompt_tokens=400`) is global, sized for CPU Ollama, and also caps fast hosted providers | B-11 |

## 4. Previous-finding recheck (model side)

| Prior item | Verdict | Evidence |
| --- | --- | --- |
| GAP-017 fine-tuning never changes production weights | **still open** (register: `built_not_proven`, `gap_register.yaml:492`) | `compose:556` `AURA_FINETUNE_TRAINER: scripted`; adapter unservable (`kernel.py:1991-2006`) |
| GAP-018 failed train recorded as success | **fixed-in-source-not-operationally-verified** | `finetune.py:447-448,508-533` (`NOT_TRAINED` terminal, nothing registered); `local_lora_trainer.py:22-27` raises instead of fabricating. `BACKEND_STATUS.md:69` still states the old behaviour (see §6) |
| GAP-020 STT English-only vs Arabic TTS | **still open** | `settings.py:1491` `tiny.en`; Arabic Piper voice `Dockerfile:73-76` |
| GAP-030 LLM by tag | **still open** | `compose:430` |
| GAP-031 embedder/STT unpinned | **still open** | `embeddings.py:358`, `whisper_stt.py:63`, `reranking.py:104` |
| GAP-012 "vllm `:latest` unpinned" | **partially fixed** | prod compose pinned (`:984`); HF Space Dockerfile still `:latest` (`Dockerfile:21`) |
| "Vision: not implemented" (`AURA_FULL_SYSTEM_REVIEW.md:327-328`) | **superseded** — now IMPLEMENTED BUT UNREACHABLE | `aura_core/vision/engine.py`; `tests/unit/test_vision_routing.py` exists; no eligible provider deployed |
| Video | **not applicable / NOT IMPLEMENTED** | `domain/llm.py:117-122` |
| Model licensing not recorded (GAP-042) | **cannot verify** in this pass | not in scope of files read |

## 5. Verification log

| Step | Tool / target | Result |
| --- | --- | --- |
| Read provider adapters, router, admission, quota, circuit, sensitivity, workload, embeddings, domain/llm | Read | lines cited above |
| Read `container.py` 160-280, 625-735, 1080-1210 | Read | provider/router/embedder/OCR/vision/STT/TTS wiring confirmed |
| Grep `lora|peft|fine_tun` in `aura_core` | Grep | 18 files; `local_lora_trainer.py`, `learning/finetune.py` read in full |
| Grep `create_model|warm_model|pull_model` outside `providers/ollama.py` | Grep | only `admin.py:565,595,611` (`list_models`, `pull_model`, `delete_model`); no caller of `create_model`/`warm_model` |
| Grep `_servable_model_tag` | Grep | `kernel.py:1988,1991`; refuses URI/path artefacts |
| Grep model/dep pins in `requirements.lock`, `Dockerfile` | Grep | `faster-whisper==1.2.1`, `ctranslate2==4.8.1`, `sentence-transformers==6.0.0`, `torch==2.13.0+cpu`, `transformers==5.15.1`, `pytesseract==0.3.13`, `onnxruntime==1.29.0`; Piper assets SHA-pinned |
| Read `deploy/spaces/inference-vllm/{Dockerfile,entrypoint.sh}` | Read | `:latest` base; `--revision` only if set; API key required |
| Grep `done_reason|finish_reason` in `providers/ollama.py` | Grep | only hard-coded `STOP`/`TOOL_CALLS` (`:170,:206`) |
| `pytest` / `python -c` | NOT RUNNABLE | C: drive full; Bash unreliable per coordinator; not attempted |
| Railway env, HF Space state, VPS state | UNKNOWN | cannot probe production; status docs quoted as claims |

## 6. Documentation inaccuracies (model side)

| Doc claim | Code / tree | Verdict |
| --- | --- | --- |
| `docs/status/BACKEND_STATUS.md:69` — "GAP-018 (a failed train is recorded as success)" | `aura_core/learning/finetune.py:447-448,508-533`; `local_lora_trainer.py:22-27` | STALE: the fix is in source (not operationally verified) |
| `CLAUDE.md` "The self-hosted LLM (`qwen2.5:3b-instruct-q4_K_M` on Ollama, CPU-only) sustains roughly 5–10 tokens/sec in production" | `BACKEND_STATUS.md:24` "no Ollama" on the live target | STALE for the Railway target; still describes the VPS compose default |
| `docs/architecture/030-hosted-open-weight-inference.md:3` "not deployed and not used: the flag defaults to off" | `BACKEND_STATUS.md:24,35` `hosted1` healthy on Railway; `control-plane.md:148-158` | STALE: deployed on Railway per status docs; still unproven (GAP-064) |
| `docs/reports/AURA_FULL_SYSTEM_REVIEW.md:327-328` "Vision: not implemented (capability flag only)" | `aura_core/vision/engine.py`, `container.py:1111-1124`, `tests/unit/test_vision_routing.py` | superseded (implemented, unreachable in prod) |
| `docs/production/gap_register.yaml` GAP-012 current_state (vllm `:latest`) | `docker-compose.prod.yml:984` pinned; `deploy/spaces/inference-vllm/Dockerfile:21` not | partially stale (already noted by the prior review) |
