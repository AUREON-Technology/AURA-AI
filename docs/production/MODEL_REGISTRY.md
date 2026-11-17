# Model Registry

Every model AURA runs in production, every model under evaluation, and how far each one is pinned.
Part of the [production roadmap](MASTER_PRODUCTION_ROADMAP.md) (Phases 2, 13 and 22). Gaps are
tracked in [gap_register.yaml](gap_register.yaml).

**Rules for this file**

- Never invent a revision SHA, benchmark number or VRAM figure. Where one hasn't been pinned or
  measured, the entry says `UNPINNED` or `NOT MEASURED`.
- **Measured** means measured by AURA on AURA's stack, with the report cited. A vendor model-card
  claim is labelled `vendor-stated` and counts as unverified until we reproduce it.
- All models are self-hosted. None runs on the Flutter client. There is no hosted-AI fallback.
  The model-emitted `tool_calls` are informational only (ADR-006), so "tool-calling support" only
  matters for how well a model emits the planner's JSON, not for dispatch.
- Status is one of `production`, `candidate`, `rejected`, plus `test-only` for models used solely
  by opt-in tests.

Verified against tree `11abb50` on 2026-09-24.

---

## 1. Production models

### 1.1 Chat / planning LLM: `qwen2.5:3b-instruct-q4_K_M` (Ollama)

| Field | Value |
| --- | --- |
| model_id | `qwen2.5:3b-instruct-q4_K_M` (Ollama library tag); upstream `Qwen/Qwen2.5-3B-Instruct` |
| revision/SHA | **UNPINNED.** Referenced by tag only (`deploy/docker-compose.prod.yml:256`, `deploy/.env.example:77-78`). `AURA_MODEL_ALLOWLIST` lists tag names, not digests (`.env.example:187`). The Ollama *server image* is digest-pinned (`docker-compose.prod.yml:667`). → GAP-030 |
| license | **VERIFY.** From recall, the upstream card for the 3B size uses the *Qwen Research License*, not the Apache-2.0 most Qwen2.5 sizes use. If that's right, production use needs a license review → GAP-042 |
| task | Chat answers, plan decomposition (`PlanningEngine.plan()`), reasoning, verification |
| modalities | Text in, text out |
| context_length | NOT MEASURED in AURA. The router checks context fit before calling (`aura_core/llm/router.py`). The effective window depends on the Ollama `num_ctx`, which isn't pinned in compose |
| quantization | Q4_K_M GGUF (from the tag) |
| VRAM requirement | N/A: CPU-only in production. RAM footprint NOT MEASURED. The container limit is 10 GB (`docker-compose.prod.yml:693`), which is a limit, not a measurement |
| tool-calling support | Emits planner JSON. Native tool-call quality NOT MEASURED |
| language coverage | vendor-stated multilingual. Arabic quality NOT MEASURED |
| benchmark results | NOT MEASURED on AURA quality suites with this model. Gated suites use scripted providers (GAP-021) |
| latency | **Measured (A4, 2026-08-23):** p50 56.3 s, p95 70.5 s full turn, n=20 sequential, 4-core/15 GB sandbox, `OLLAMA_NUM_PARALLEL=1` ([phase-a4-load-test.md](../reports/phase-a4-load-test.md)). G2: ~480 ms per short completion on the same class of hardware (roadmap Phase G2) |
| throughput | **Measured (A4):** 0.04–0.06 turns/s, flat from 1 to 8 concurrent. Token rate is *documented* as ~5–10 tok/s in production (CLAUDE.md) but has no report |
| known limitations | Single in-flight generation (GAP-011). 700-token planner ceiling tuned to the 180 s timeout (`aura_core/planning/engine.py`). `SystemPromptLeakGuard` fired on 3/15 real turns in A4, unresolved (GAP-022) |
| status | **production** |

### 1.2a Embeddings: `intfloat/multilingual-e5-small` (compose default since the Arabic plan, stage 4)

| Field | Value |
| --- | --- |
| model_id | `intfloat/multilingual-e5-small`, 384-dim (both compose files; `deploy/.env.example`; Railway is a dashboard variable). E5 `query: ` / `passage: ` prefixes applied automatically (`AURA_EMBEDDING_QUERY_PREFIX` / `_DOCUMENT_PREFIX`); the document prefix is part of the index identity |
| revision/SHA | UNPINNED (GAP-031), like the model it replaces |
| language coverage | Multilingual, incl. Arabic |
| benchmark results | [ARABIC_RETRIEVAL_EVAL_2026-11-17.md](../reports/ARABIC_RETRIEVAL_EVAL_2026-11-17.md): with the `either` gate, recall@5 0.95 English / 0.90 Arabic-asks-English / 0.65 Arabic (bge-small-en with the same gate: 0.95 / 0.80 / 0.65) |
| rollout | A new index: `scripts/rebuild_index.py` after the first deploy |
| status | **built, not proven** (offline eval only; not deployed) |
| future | `BAAI/bge-m3` (568M parameters, 1024-dim, roughly 5× the RAM/CPU, a new collection): stronger Arabic, documented as the next step (owner decision 10), not built |

### 1.2 Embeddings: `BAAI/bge-small-en-v1.5` (sentence-transformers, in-process) -- previous default

| Field | Value |
| --- | --- |
| model_id | `BAAI/bge-small-en-v1.5` (`docker-compose.prod.yml:289`, `AURA_EMBEDDING_PROVIDER=sentence-transformers`) |
| revision/SHA | **UNPINNED.** Loaded with `SentenceTransformer(model_name)` and no `revision=` (`aura_core/llm/embeddings.py:358`). Downloaded from the HF Hub at first use (`HF_TOKEN` comment, `docker-compose.prod.yml:291-293`) → GAP-031 |
| license | MIT (vendor-stated, from recall; re-verify at pin time) |
| task | Dense retrieval embeddings (Qdrant) |
| modalities | Text |
| context_length | vendor-stated 512 tokens. Unverified in-tree |
| quantization | None (fp32, CPU, torch `2.13.0+cpu`) |
| VRAM requirement | N/A (CPU). RAM NOT MEASURED |
| tool-calling support | N/A |
| language coverage | English (model name). Arabic retrieval quality NOT MEASURED, expected poor |
| benchmark results | NOT MEASURED with this model on AURA's corpus. The A3 baseline (recall@5 0.65, nDCG 0.51, MRR 0.37) used the *hash* embedder, not this one ([phase-a3-retrieval-corpus.md](../reports/phase-a3-retrieval-corpus.md)) |
| latency | NOT MEASURED in isolation |
| throughput | NOT MEASURED |
| known limitations | Dimension 384 is baked into `IndexIdentity`. A silent upstream revision change would drift vectors under an existing index. A4 found and fixed an id-slug mismatch that had made retrieval return nothing with this embedder |
| status | **production** |

### 1.3 Reranker

| Field | Value |
| --- | --- |
| model_id | Production default is the **heuristic** reranker (`KNOWLEDGE_RERANK_PROVIDER` default `heuristic`, `aura_core/config/settings.py:394,439`), which isn't a model. The opt-in `cross-encoder/ms-marco-MiniLM-L-6-v2` (`settings.py:395`) isn't enabled in compose |
| revision/SHA | Cross-encoder: UNPINNED (GAP-031) |
| license | Cross-encoder: Apache-2.0 (vendor-stated) |
| task | Rerank fused BM25+dense candidates |
| modalities | Text |
| context_length | NOT MEASURED |
| quantization | None |
| VRAM requirement | N/A |
| tool-calling support | N/A |
| language coverage | English |
| benchmark results | NOT MEASURED (heuristic vs cross-encoder never compared on AURA data) |
| latency / throughput | NOT MEASURED |
| known limitations | No learned reranking in production |
| status | heuristic: **production**. Cross-encoder: **candidate** (opt-in, unevaluated) |
| update (Arabic plan, stage 4) | Compose default is now `AURA_KNOWLEDGE_RERANK_PROVIDER=either` with `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual): a candidate passes if the heuristic **or** the cross-encoder accepts it (`EitherGateReranker`, decision 17). Measured in [ARABIC_RETRIEVAL_EVAL_2026-11-17.md](../reports/ARABIC_RETRIEVAL_EVAL_2026-11-17.md). Latency NOT MEASURED on the production host (expected 100-300 ms CPU per search). Status: **built, not proven** |

### 1.4a Speech-to-text: faster-whisper `small` (code default since the Arabic plan, stage 4)

| Field | Value |
| --- | --- |
| model_id | `small` (`VoiceSettings.stt_model`), multilingual: live voice accepts Arabic |
| benchmark results | [STT_WER_ARABIC_ENGLISH_2026-11-17.md](../reports/STT_WER_ARABIC_ENGLISH_2026-11-17.md), FLEURS: English WER 6.46% (tiny.en 11.96%), Egyptian Arabic 24.96%; RTF ~0.3 on 4 dev CPU cores (tiny.en ~0.1) |
| status | **built, not proven** (GAP-020 closes after a production-host run) |
| future | Whisper `large-v3-turbo` on the GPU inference plane for Egyptian dialect quality (decision 11), not built |

### 1.4 Speech-to-text: faster-whisper `tiny.en` -- previous default

| Field | Value |
| --- | --- |
| model_id | `tiny.en` (`VOICE_STT_MODEL`, `aura_core/config/settings.py:1048,1080`), loaded as `WhisperModel("tiny.en")` (`aura_core/adapters/whisper_stt.py:63`) |
| revision/SHA | **UNPINNED.** Fetched by size name at runtime (GAP-031) |
| license | MIT (Whisper and faster-whisper, vendor-stated) |
| task | ASR for `/voice/transcribe` and live voice |
| modalities | Audio in, text out |
| context_length | 30 s windows (Whisper architecture, vendor-stated) |
| quantization | CTranslate2, compute type per adapter config. NOT MEASURED |
| VRAM requirement | N/A (CPU) |
| tool-calling support | N/A |
| language coverage | **English only.** Arabic speech input unsupported by default (GAP-020) |
| benchmark results | WER NOT MEASURED. Phase F2 proved a synthesize→transcribe round trip works, but didn't measure WER |
| latency / throughput | NOT MEASURED |
| known limitations | Smallest Whisper size. Energy-based VAD (`aura_core/voice/vad.py`) |
| status | **production** |

### 1.5 Text-to-speech: Piper (primary) + espeak-ng (fallback)

| Field | Value |
| --- | --- |
| model_id | Piper `en_US-lessac-medium`, `ar_JO-kareem-medium` (`settings.py:1057-1058`), Piper binary `2023.11.14-2` |
| revision/SHA | **Pinned.** The binary and both voices' `.onnx`/`.onnx.json` are sha256-verified at image build (`aura_backend/Dockerfile:44-80`). Voices are from `rhasspy/piper-voices` tag `v1.0.0`. espeak-ng comes from apt (unpinned). Tashkeel model `/opt/piper/libtashkeel_model.ort` ships inside the pinned Piper tarball |
| license | Piper MIT. Voice licenses per each voice's MODEL_CARD: UNVERIFIED. espeak-ng GPL-3.0 (invoked as a subprocess) |
| task | TTS for `/voice/speak` and live voice |
| modalities | Text in, audio out |
| context_length | N/A |
| quantization | ONNX (medium quality voices) |
| VRAM requirement | N/A (CPU) |
| tool-calling support | N/A |
| language coverage | English, Arabic (Jordanian voice, tashkeel diacritisation) |
| benchmark results | MOS / intelligibility NOT MEASURED |
| latency / throughput | NOT MEASURED |
| known limitations | The Piper upstream repo is archived (Dockerfile comment). **Piper stays the Arabic TTS** even if Qwen3-TTS is adopted for English, because Qwen3-TTS is not listed as supporting Arabic (re-verify at evaluation time) |
| status | **production** |

### 1.6 OCR: Tesseract (not a neural model registry item, listed for completeness)

`tesseract-ocr` from Debian apt in the gateway image (`aura_backend/Dockerfile:60`), unpinned,
with `KNOWLEDGE_OCR_LANG=eng` by default (`settings.py:447`). Proven on one scanned PDF end to end
(roadmap Phase F1). Status: **production**. It extracts text only and doesn't understand images
(GAP-019).

### 1.7 Test-only and configured-but-never-run

| model_id | Where | Status |
| --- | --- | --- |
| `smollm2:135m` (Ollama) | Opt-in `tests/load/test_multi_model_routing.py` (Phase G2) | **test-only** |
| `hash-embed-v1` | In-code default embedder (`settings.py:317`), refused outside dev/test by `AuraSettings.validate()` | **test-only** |
| `Qwen/Qwen2.5-32B-Instruct-AWQ` | Default `AURA_VLLM_MODEL` for the `gpu` compose profile (`docker-compose.prod.yml:713`). Never run, no GPU host exists | **candidate** (superseded by the Qwen3 set below) |
| `Qwen/Qwen2.5-7B-Instruct-AWQ` | Example `AURA_PROVIDER_HFSPACE_MODEL` (`.env.example:132`). Remote failover, off by default (GAP-034) | **rejected for production**: rented off-host inference conflicts with the "no hosted-AI fallback" rule unless an ADR says otherwise |

---

## 2. Candidate set (GPU inference plane, Phase 2)

None of these has been downloaded, run or measured by AURA. For every row, **revision/SHA is
UNPINNED** and **VRAM, benchmark results, latency and throughput are NOT MEASURED**. Those values
get filled in from AURA's own Phase 2 bake-off reports, never from model cards. `vendor-stated`
entries come from the upstream model family's published positioning and must be re-verified
against the pinned revision's `config.json` and model card.

| model_id | task | modalities | license | revision/SHA | quantization | VRAM | context_length | tool-calling | language coverage | benchmarks | latency | throughput | known limitations | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen/Qwen3-30B-A3B-Instruct-2507 | General chat/planning (MoE, ~3B active, vendor-stated) | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | TBD (FP8/AWQ candidates) | NOT MEASURED | vendor-stated long context, verify | vendor-stated | multilingual incl. Arabic (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | MoE memory is total-param sized, not active-param sized | candidate |
| Qwen/Qwen3-Next-80B-A3B-Instruct | High-quality chat tier | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | TBD | NOT MEASURED | vendor-stated long context, verify | vendor-stated | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | Newer architecture: check vLLM support at the pinned vLLM version | candidate |
| Qwen/Qwen3-Coder-Next | Engineer persona / code | text | verify | UNPINNED | TBD | NOT MEASURED | verify | vendor-stated | code + English | NOT MEASURED | NOT MEASURED | NOT MEASURED | Must be evaluated on AURA's engineer tasks, not public leaderboards | candidate |
| Qwen/Qwen3-Coder-30B-A3B-Instruct | Engineer persona / code (smaller) | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | TBD | NOT MEASURED | verify | vendor-stated | code + English | NOT MEASURED | NOT MEASURED | NOT MEASURED | — | candidate |
| Qwen/Qwen3-VL-8B-Instruct | Vision / document understanding (GAP-019) | text + image (video vendor-stated) | Apache-2.0 (vendor-stated, verify) | UNPINNED | TBD | NOT MEASURED | verify | vendor-stated | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | Needs a kernel image path, a trust channel for image content, and upload limits | candidate |
| Qwen/Qwen3-Embedding-0.6B | Embeddings | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | none/fp16 | NOT MEASURED | verify | N/A | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | Changing the embedder means a new `IndexIdentity`, a reindex and a promotion gate (ADR-007) | candidate |
| Qwen/Qwen3-Embedding-4B | Embeddings | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | fp16 | NOT MEASURED | verify | N/A | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | same | candidate |
| Qwen/Qwen3-Embedding-8B | Embeddings | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | fp16 | NOT MEASURED | verify | N/A | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | same | candidate |
| Qwen/Qwen3-Reranker-0.6B | Reranking | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | fp16 | NOT MEASURED | verify | N/A | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | Must beat the heuristic reranker on AURA's corpus to ship | candidate |
| Qwen/Qwen3-Reranker-4B | Reranking | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | fp16 | NOT MEASURED | verify | N/A | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | same | candidate |
| Qwen/Qwen3-Reranker-8B | Reranking | text | Apache-2.0 (vendor-stated, verify) | UNPINNED | fp16 | NOT MEASURED | verify | N/A | multilingual (vendor-stated) | NOT MEASURED | NOT MEASURED | NOT MEASURED | same | candidate |
| Qwen/Qwen3-ASR-0.6B | ASR | audio → text | verify | UNPINNED | TBD | NOT MEASURED | N/A | N/A | multilingual incl. Arabic: verify | WER NOT MEASURED | NOT MEASURED | NOT MEASURED | Serving stack (vLLM or custom) to confirm | candidate |
| Qwen/Qwen3-ASR-1.7B | ASR | audio → text | verify | UNPINNED | TBD | NOT MEASURED | N/A | N/A | multilingual incl. Arabic: verify | WER NOT MEASURED | NOT MEASURED | NOT MEASURED | same | candidate |
| Qwen/Qwen3-TTS-0.6B | TTS (English) | text → audio | verify | UNPINNED | TBD | NOT MEASURED | N/A | N/A | **No Arabic** (per brief; re-verify). Piper stays for Arabic | MOS NOT MEASURED | NOT MEASURED | NOT MEASURED | Arabic gap | candidate |
| Qwen/Qwen3-TTS-1.7B | TTS (English) | text → audio | verify | UNPINNED | TBD | NOT MEASURED | N/A | N/A | **No Arabic** (per brief; re-verify). Piper stays for Arabic | MOS NOT MEASURED | NOT MEASURED | NOT MEASURED | Arabic gap | candidate |

### Admission rule for a candidate to become `production`

1. The revision is pinned to an immutable SHA and recorded here, and the weights are mirrored
   onto the inference host (no runtime Hub fetch).
2. The license has been reviewed and recorded.
3. The serving stack version is pinned: a vLLM image by digest (the current compose reference
   `vllm/vllm-openai:latest` is unpinned, GAP-032).
4. It has been measured on AURA's hardware: VRAM at target concurrency, TTFT/answer p50/p95/p99
   across the Phase 3 load matrix, and throughput.
5. It has been measured on AURA's eval suites (retrieval, answer groundedness, agent tasks, guard
   false-positive rate) and has passed `RegressionGate` against the current production model.
6. Rollout is canary (Phase 24), with a rollback path through the router provider list.

## 3. Pinning state summary

| Artefact | Pinned? | Where | Gap |
| --- | --- | --- | --- |
| Ollama server image | digest | `docker-compose.prod.yml:667` | — |
| Chat model `qwen2.5:3b-instruct-q4_K_M` | **tag only** | compose `:256`, `.env.example:77-78` | GAP-030 |
| Embedder `BAAI/bge-small-en-v1.5` | **no revision** | `embeddings.py:358` | GAP-031 |
| Cross-encoder (opt-in) | **no revision** | `settings.py:395` | GAP-031 |
| Whisper `tiny.en` | **no revision** | `whisper_stt.py:63` | GAP-031 |
| Piper binary + EN/AR voices | sha256 | `aura_backend/Dockerfile:44-80` | — |
| vLLM image | **`:latest`, no digest** | compose `:709` | GAP-032 |
| vLLM model `Qwen2.5-32B-Instruct-AWQ` | **no revision** | compose `:713` | GAP-030 |
| Tesseract / espeak-ng | apt, unpinned | `aura_backend/Dockerfile:60-61` | GAP-032 |
