# Plan: enable built runtime features on Railway, then build Arabic/live voice, vision and ops

**Status:** Draft, awaiting owner approval · **Date:** 2026-11-17 · **Base:** `main@446e004`
(the SHA live on Railway) · **Branch:** `anas/laughing-hamilton-glfevj`

## Goal

Use what AURA has already built but leaves switched off on the Railway production target
(`api.aureon-ai.tech`). Then close the three areas the owner chose: Arabic and live voice, vision,
and reliability and operations. Each item names the evidence that would prove it. "Configured" is not
"proven" (CLAUDE.md standing rule).

## What the live probe showed (2026-11-17, `GET https://api.aureon-ai.tech/health/ready`)

- `healthy: true`, schema `0021`, release `446e004`, `hosted1` healthy (46 ms), `hfspace` HTTP 503.
- **The knowledge index is from the wrong embedder.** The alias points at
  `aura-knowledge__v1__hash-embed-v1__384`, but the embedder is `BAAI/bge-small-en-v1.5`
  (`semantic: true`). `IndexManager.resolve_for_read()` refuses a model mismatch, and
  `KnowledgeEngine.retrieve()` catches that and returns `strategy="unavailable"`
  (`aura_core/knowledge/engine.py:563`). **Document retrieval (RAG) is silently off for every
  turn in production.** Deleting a document goes through the same check and raises.
- `aureon-ai.tech` (apex) and both `*.up.railway.app` hosts in the docs answer with Railway's
  `404 Application not found`. `api.aureo-ai.tech` (no "n") answers 502. The live host is
  `api.aureon-ai.tech`.
- `voice`: `piper-en+piper-ar` TTS, `faster-whisper-tiny.en` STT, `live: false`.

## User decisions (do not re-ask)

Recorded 2026-11-17.

1. Production is `api.aureon-ai.tech` (Railway). The apex 404 is a separate DNS/domain task, not part
   of this plan.
2. **SENSITIVE generations may go to Groq.** `hosted1` is raised to `max_data_classification=sensitive`
   instead of reviving `hfspace` for that role. The owner accepts that memory, personal-OS and
   document content reaches a third party that does not train on it. The backend allows this
   because `allow_prompt_training=false`, and it logs a boot warning (`settings.py:649`).
3. **A second hosted slot `hosted2` = Cloudflare Workers AI**: config only, the host is already
   sanctioned. Cerebras was considered and rejected: it is deliberately absent from
   `HOSTED_OPEN_WEIGHT_HOSTS`.
4. **Vision is served by the HF Space running `Qwen2.5-VL-7B`** (vLLM, paid GPU tier). Images never go
   to Groq or Cloudflare: the router refuses third-party image turns (ADR-031, `router.py:347`).
5. **STT moves to multilingual Whisper `base`**, still on Railway's CPU.
6. **Operations target Grafana Cloud (free)**: metrics scrape, OTLP traces and synthetic uptime checks.
7. Priority areas for new work, in this order: **voice (Arabic + live), vision, reliability and
   operations**. Deferred: workspaces/teams UI, Track AP, the GPU inference plane, branch protection
   (the owner's own setting).

## Phase 0: fix and enable on Railway (owner actions, no code)

Every item is a Railway variable change or an admin call. They are listed in the order to apply them.

| # | Action | Variable / call | Evidence it worked |
|---|---|---|---|
| 0.1 | Pin the embedder before re-indexing, so the index is built once | `AURA_EMBEDDING_MODEL_REVISION=<bge-small-en-v1.5 commit SHA>` | `/health/ready` → `embedding.revision` non-null |
| 0.2 | Rebuild and promote the knowledge index | `POST /api/v3/admin/index/rebuild` as SuperAdmin | Response `promoted: true`; `/health/ready.index` = `…baai-bge-small-en-v1-5…__384`; a chat turn about an uploaded document cites it. If the gate refuses (`gate_failures`), stop and report: it compares recall against the old index. |
| 0.3 | Allow sensitive on Groq | `AURA_PROVIDER_HOSTED1_MAX_DATA_CLASSIFICATION=sensitive`; in Groq's console, turn off I/O log retention (Data Controls) | Boot log names the warning; memory extraction succeeds (a remembered fact shows on `/api/v3/memory`) |
| 0.4 | Add `hosted2` (Cloudflare Workers AI) | `AURA_LLM_PROVIDERS=hosted1,hosted2,hfspace`; `AURA_PROVIDER_HOSTED2_KIND=openai_compatible`, `_BASE_URL=https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1`, `_MODEL=@cf/openai/gpt-oss-120b` (or another open-weight `@cf/` model), `_API_KEY_ENV`, `_MAX_DATA_CLASSIFICATION=sensitive`, `_QUALITY_TIER=3`, `_CONTEXT_WINDOW`, `_MAX_OUTPUT_TOKENS=1024`, `_TIMEOUT_S=60` | `/health/ready.providers.hosted2.healthy: true`; with Groq rate-limited, a turn is served by `hosted2` (telemetry `provider`) |
| 0.5 | Raise prompt budgets sized for a CPU 400-token prompt (two hosted slots now share load) | `AURA_KNOWLEDGE_TOP_K=4`, `AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS=700`, `AURA_CAG_MAX_TOKENS=240` (`_GLOBAL_TOKENS=120`, `_USER_TOKENS=120`), `AURA_MEMORY_TOKEN_BUDGET=200`, `AURA_MEMORY_PROMPT_HISTORY_MESSAGES=6` (variable names verified against `settings.py` before applying) | Answers cite more than one chunk; no rise in 429s on `/metrics` |
| 0.6 | Cross-encoder rerank | `AURA_KNOWLEDGE_RERANK_PROVIDER=cross_encoder`, `AURA_KNOWLEDGE_RERANK_MODEL_REVISION=<sha>` | Retrieval p95 measured before and after (Phase R6) |
| 0.7 | Turn safety caps | `AURA_LLM_TURN_GENERATION_BUDGET_S=120`, `AURA_LLM_STREAM_MAX_S=180`, `AURA_LLM_USER_MAX_CONCURRENT_GENERATIONS=2` | A third concurrent stream from one user queues |

Kept as they are, on purpose: `retrieval_strategy=simple` until R6 measures `adaptive`;
`complexity_routing=false` (no `reasoning` pool exists); sandbox and fine-tuning (need Docker or a GPU host).

## Phase V: Arabic and live voice (code + config)

### Backend (`aura_backend/`)
- **Config:** `AURA_VOICE_STT_MODEL=base`, `AURA_VOICE_STT_MODEL_REVISION=<sha>`,
  `AURA_VOICE_STT_WARM_ON_STARTUP=true`, then `AURA_VOICE_LIVE_ENABLED=true`. Memory: about
  150 MB per uvicorn worker; check the Railway plan's RAM against `AURA_WORKERS`.
- **`POST /api/v3/voice/transcribe` accepts an optional `language` form field**, validated with the
  same `normalise_language` the live session uses. When it is absent, a multilingual model
  auto-detects. The response adds `language` (detected or requested). Additive: old clients
  send nothing and get today's shape plus one field.
- **Neural VAD:** a `SileroVad` adapter behind the existing `EnergyVad` interface
  (`aura_core/voice/vad.py`), using the Silero ONNX model faster-whisper already bundles (no new
  dependency: `faster-whisper` and `onnxruntime` are locked). New setting
  `AURA_VOICE_LIVE_VAD=energy|silero`, default `energy`, so nothing changes until it is chosen.
  It falls back to `energy` with a warning if the model cannot load.
- **WER measurement:** `scripts/measure_stt_wer.py` runs a small labelled English and Arabic clip set
  (licence-clean, e.g. Common Voice CC0) through the configured model and writes
  `docs/reports/STT_WER_<date>.md`. This is the GAP-020 evidence; it is opt-in and not part of the
  default `pytest`.
- **Tests:** transcribe language validation (a bad code → 422, `ar` reaches the engine); the
  Silero/energy selection and fallback; live session with `language="ar"`.

### Client (`aura_client/`)
- `live_voice_view_model.dart:81` hardcodes `language: 'en'`. Take it from the existing
  `core/ai/voice/speech_language.dart` (app locale, with a manual override), so Arabic users start
  an Arabic session.
- `voice_recorder_button.dart` / `VoiceClient.transcribe`: send the same language.
- When live is off, the server refuses the session. The client must show the existing "live voice
  unavailable" state, not a spinner (to be checked).
- Tests: view-model test that the locale `ar` → start frame `language: 'ar'`; a voice-client
  request test. Run `flutter analyze` and `flutter test` with the pinned SDK (CLAUDE.md recipe).

## Phase I: vision (mostly configuration + proof)

- **Space (`deploy/spaces/inference-vllm/`):** GPU hardware (L4 or A10G, no sleep while on trial),
  `VLLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct-AWQ` with its revision pinned, `MAX_IMAGES=4`. The
  entrypoint already passes `--limit-mm-per-prompt` (`entrypoint.sh:95`), so no Dockerfile
  change is expected.
- **Railway:** `AURA_PROVIDER_HFSPACE_MODEL` and `_MODEL_REVISION` to match,
  `AURA_PROVIDER_HFSPACE_MODALITIES=text,image`, `AURA_VISION_ENABLED=true`. Qwen2.5-VL also
  serves text, so `hfspace` stays a text fallback.
- **Code (only if a gap shows):** the client already sends `images` (`sse_agent_client.dart`). Check
  that a turn with images while `hfspace` sleeps returns a clear "vision unavailable" error that
  the client shows (no silent text-only answer). Add a test if not.
- **Licence:** record Qwen2.5-VL-7B (Apache-2.0) under GAP-042.
- **Proof:** a live image turn (photo → description), the router's decision in telemetry showing
  `hfspace`, and a turn with an image while only Groq is up showing `third_party_image` refusal.
  The report goes in `docs/reports/`.

## Phase R: reliability and operations (Grafana Cloud)

| # | Item | Work | Closes / evidence |
|---|---|---|---|
| R1 | Metrics | `AURA_METRICS_TOKEN` on Railway; a Grafana Cloud *Metrics Endpoint* scrape of `https://api.aureon-ai.tech/metrics` with a bearer token. Confirm `PROMETHEUS_MULTIPROC_DIR` is set on Railway, otherwise each scrape sees one of `AURA_WORKERS` processes (`observability/metrics.py:94`); fix it in the image if not | GAP-036 (half) |
| R2 | Traces | `AURA_TRACING_ENABLED=true`, `AURA_OTLP_ENDPOINT=<Grafana OTLP gateway>`, `OTEL_EXPORTER_OTLP_HEADERS` as a Railway secret | Traces visible per turn |
| R3 | Uptime | Grafana Synthetic Monitoring: `/health/live` (200) and `/health/ready` (`healthy == true`) every minute, from two regions | GAP-036 external probe |
| R4 | Dashboards + alerts as code | `deploy/observability/grafana-cloud/`: dashboard JSON (turn latency, provider health, 429s, admission queue) and alert rules adapted from the VPS Prometheus rules; runbook in `docs/deployment/observability.md` | GAP-037 buckets checked against measured latency |
| R5 | Backups | Create the Railway backup cron service per `docs/deployment/backups.md` with two offsite targets; then a restore drill into a scratch Postgres/Qdrant, recorded in `docs/reports/` | GAP-028 / GAP-046 |
| R6 | Real-model eval | Run `real-model-eval.yml` (or locally with the keys) against `hosted1`+bge: simple vs adaptive retrieval, heuristic vs cross-encoder rerank. Keep whichever wins on the RAG SLO; set it on Railway | GAP-021, GAP-023 |
| R7 | Load | 1→10→25 virtual users against `api.aureon-ai.tech` (test account, `tests/load` harness), with Groq and Cloudflare limits stated. GAP-041 stays **open**: 50 users is beyond the free tiers by arithmetic | Partial evidence only, stated as such |

## Contract (all additive)

- `POST /api/v3/voice/transcribe`: optional form field `language`; response adds `language`.
- `/health/ready` and provider names: `hosted2` appears in `providers`.
- Live voice `start` frame: `language` already exists. The client now sends the real value.
- No migrations. No endpoint or field removed.

## Rollout order

1. Phase 0 (0.1 → 0.2 first: restores RAG), then 0.3–0.7.
2. R1–R3 before Phase V/I, so their effects are measured.
3. Phase V backend merge → Railway deploy → client release. An older client keeps sending `en`, which
   still works.
4. Phase I once the Space GPU is provisioned.
5. R4–R7 alongside.

## Verification

- Backend: `pytest`, `pytest tests/architecture -q`, `TESTING=True python -m unittest discover -p
  "test_*.py"`. Local results are reported as local (CI runner availability: CB-44).
- Client: `flutter analyze` + `flutter test` with Flutter 3.44.6, counts quoted.
- Live: each Phase 0 row's evidence column; then the owner's manual checklist (voice EN/AR, live
  barge-in, image turn, uptime alert firing on a forced failure).

## Out of scope

Workspaces/teams UI (ADR-034 clients), Track AP (GAP-058…062), GPU inference plane (GAP-011/012),
Engineer sandbox on Railway (no Docker), fine-tuning, Claude provider (ADR-033), apex domain fix,
branch protection.
