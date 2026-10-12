# Plan: Free Hosted Open-Weight Inference for AURA

**Status:** Proposed. Step 0 is drafted as
[ADR-030](../architecture/030-hosted-open-weight-inference.md) (Proposed). Phase 1 (the policy
layer) is **built and tested, not deployed**. Phases 2–6 are not started.
**Date of research:** 2026-10-11. Free-tier terms in this space change monthly (see §2's change
log); re-verify every number against the provider's own console before acting on it.

## 1. The question and the short answer

> Can AURA use open-source LLMs **without downloading and running them on our own servers**, for
> free, and what would it take to switch?

**Yes, technically, with almost no adapter work** — every viable free host exposes the same
OpenAI-compatible `POST /v1/chat/completions` that `aura_core/llm/providers/openai_compatible.py`
already speaks, and `ProviderRouter` already does multi-provider failover, circuit breaking and
`429`-as-retryable (`aura_core/llm/providers/base.py:189,208`).

**But today the platform deliberately refuses it.** Three things stand in the way, and they are
policy, not missing code:

| Blocker | Where | What it does |
| --- | --- | --- |
| Banned host list | `aura_core/config/external_inference.py` → `EXTERNAL_INFERENCE_HOSTS` | Already lists `api.groq.com`, `openrouter.ai`, `api-inference.huggingface.co`, `api.together.xyz`, `api.deepinfra.com`, … `ProviderConfig._reject_external_base_url` refuses them at boot, **with no override**. |
| Banned credential names | same file → `BANNED_CREDENTIAL_NAMES` | `GROQ_API_KEY`, `OPENROUTER_API_KEY`, … fail `test_no_external_inference.py` if they appear in any manifest — and the scan is a substring match, so `AURA_PROVIDER_GROQ_API_KEY` fails too. |
| Fitness tests | `aura_backend/tests/architecture/test_no_external_inference.py` | Six static layers + a runtime check. CLAUDE.md calls self-hosting "a fitness-tested invariant, not a preference". |

The repo already has the right *shape* for an exception: `REMOTE_INFERENCE_HOSTS` (HF Spaces,
Cloud Run, Railway, Fly, Modal) is a **gated** category — refused unless
`AURA_ALLOW_REMOTE_INFERENCE=true`, warned on every boot. This plan proposes a third, separately
gated category for *hosted open-weight* APIs, rather than deleting the guardrails.

### What switching actually costs (the honest trade-off)

| | Today (Ollama, `qwen2.5:3b` CPU) | Free hosted open-weight |
| --- | --- | --- |
| Quality | 3B model | 20B–120B models (e.g. `gpt-oss-120b`, Qwen3, Llama 3.3 70B, Gemma 4) |
| Speed | ~5–10 tok/s; 700-token plan can take minutes | Hundreds of tok/s on Groq-class hardware |
| Capacity | Bounded by one CPU; effectively unlimited per day | **Hard daily quotas** — realistically ~100–250 chat turns/day total across all free tiers (§4) |
| Privacy | Prompts, retrieved documents and memory never leave the VPS | **They leave** — every prompt, RAG chunk, memory snippet and Engineer-workspace file excerpt goes to a third party |
| Offline | Works without internet | Stops without internet (unless local fallback is kept) |
| Stability | Model pinned by digest | Free models get withdrawn without notice (OpenRouter pulled one on 2026-10-06; Groq retired Llama 3.3 70B / 3.1 8B in 2026-06) |
| Cost | VPS RAM/CPU | $0 until a quota is hit; then errors, not bills |

**Recommendation:** do it as **hosted-primary with local fallback**, not hosted-only. Keep a small
Ollama model as the last candidate so a quota exhaustion, provider outage or model withdrawal
degrades quality instead of taking chat down. Treat free tiers as suitable for development,
demos and small-household scale — not as the production capacity plan.

## 2. Research: free ways to use open-weight LLMs without hosting them

Two categories exist, and the platform already distinguishes them:

- **A. Hosted open-weight APIs** — someone else runs the model; you send prompts over HTTPS.
  This is what the request is about. Currently **banned** in AURA.
- **B. Free rented compute running *your* container** — e.g. a free Hugging Face CPU Space
  running llama.cpp. Already **gated** (allowed with `AURA_ALLOW_REMOTE_INFERENCE=true`) and
  documented in `docs/deployment/inference-plane.md`. Free CPU Spaces are no faster than the VPS
  and sleep when idle, so this moves the load but does not improve quality — mentioned only for
  completeness.

### 2.1 Category A — providers, as of 2026-10

All base URLs below are OpenAI-compatible unless noted, so they work with
`kind=openai_compatible` and no new adapter.

| Provider | Base URL | Free allowance | Open-weight models on free tier | Data handling | Verdict |
| --- | --- | --- | --- | --- | --- |
| **Groq** | `https://api.groq.com/openai/v1` | Permanent free plan. Published base limits: **30 RPM, 1,000 RPD, 8K TPM, 200K TPD per model** (`gpt-oss-120b`, `gpt-oss-20b`, `qwen3.8-27b`). Exact free limits shown per-org in console. No card. | gpt-oss-120b/20b, Qwen3.8 27B (+ Whisper for ASR) | States it does not train on customer data; I/O logs kept ≤30 days for abuse/reliability, opt-out in Data Controls. | **Primary.** Best quality+speed for free; per-model quotas stack. |
| **Cloudflare Workers AI** | `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1` | **10,000 Neurons/day**, resets 00:00 UTC; text-gen ≤300 RPM. Errors (not bills) on the free plan when exceeded. | `@cf/openai/gpt-oss-20b`, `@cf/qwen/qwen3-30b-a3b-fp8`, `@cf/google/gemma-3-12b-it`, Llama, Mistral | Cloudflare platform terms; token needs `Workers AI: Read`. | **Secondary.** Predictable daily budget; smaller models. |
| **OpenRouter** (`:free` models) | `https://openrouter.ai/api/v1` | **20 RPM, 50 RPD** (1,000 RPD after a one-time $10 credit purchase). | Rotating set: Gemma 4 26B/31B, gpt-oss, Llama, Nemotron… | Free endpoints are served by third-party hosts; some free endpoints require allowing prompt logging/training in privacy settings. Mixed catalog — also hosts closed models. | **Tertiary / overflow.** Catalog churns; pin nothing long-term. |
| **Google AI Studio** | `https://generativelanguage.googleapis.com/v1beta/openai/` (beta) | Standing free tier; limits now shown only in AI Studio. | Gemma 4 (open-weight); the rest of the catalog is closed Gemini | **Free-tier data may be used to improve Google products** (outside EU/UK/EEA). | Not recommended: closed-model catalog on the same key, training on inputs, and Gemma via the OpenAI-compat layer is undocumented. |
| **Hugging Face Inference Providers** | `https://router.huggingface.co/v1` | **$0.10/month** credit for free accounts (≈ a handful of requests); `402` when exhausted. PRO $2/month. | Very broad | Routed to partner providers. | Not viable as free capacity. Useful only for testing a model before choosing it. |
| **SambaNova Cloud** | OpenAI-compatible | **20 RPM, 20 RPD**, 200K TPD per model. | Llama 3.3 70B, DeepSeek-V3.1 | — | Too few requests/day to matter. |
| **NVIDIA API Catalog** (build.nvidia.com) | `https://integrate.api.nvidia.com/v1` | Trial credits; ~40 RPM reported by users; no official free limits published. | Nemotron, Llama, Qwen, … | Trial/evaluation terms. | Evaluation only. |
| **Mistral** (Experiment plan) | `https://api.mistral.ai/v1` | Free plan exists; terms in flux (2026-10-08 wording change). | Mistral Small / open models | **Requires opting in to training on your requests**, phone verification. | Reject — training on inputs is incompatible with AURA's document/memory content. |
| **Cerebras** | — | **No permanent free tier since 2026-07-16**: $5/30-day credit, card required. | — | — | Reject (not free). |
| **GitHub Models** | — | **Retired 2026-07-30.** | — | — | Gone. |
| Fireworks, Nebius, DeepSeek, AI21, Together | — | One-time trial credits only. | — | — | Not free capacity. |

Sources: provider docs and two independently maintained trackers (links in §9). Where they
disagreed, the newer and/or first-party figure is used; "≈" marks a figure taken from a tracker,
not a first-party page.

## 3. Target architecture

Nothing above the router changes. `AIKernel` → `ProviderRouter` → N `openai_compatible`
providers, plus the existing local one as last resort:

```
            ┌─────────────── ProviderRouter (policy: quality_first) ───────────────┐
 AIKernel → │ groq_a  (gpt-oss-120b, tier 3)  ── 429/quota → cooldown until reset   │
 (plan,     │ groq_b  (qwen3.8-27b,  tier 3)  ── separate per-model quota           │
  execute,  │ cfai    (gpt-oss-20b,  tier 2)  ── 10k neurons/day                    │
  generate) │ orfree  (a :free model, tier 2) ── 50 RPD                             │
            │ local   (ollama qwen2.5:3b, tier 1, is_local) ── always available     │
            └──────────────────────────────────────────────────────────────────────┘
 Embeddings (sentence-transformers), ASR (Whisper), TTS (Piper), OCR, reranking: UNCHANGED, local.
 Flutter client: UNCHANGED — it only ever talks to /api/v3/*.
```

Keeping embeddings local is deliberate: they run fine on CPU, they are called on every ingest and
query (would burn quota fast), and re-embedding the corpus with a different hosted model would
force a full re-index (ADR-007).

## 4. Capacity math — what "free" buys

A typical AURA chat turn is ≥2 LLM calls (plan + generate), each carrying system prompt, history
and DATA-fenced retrieval context. Assume **~7K tokens per turn** (≈3K in + ~0.5K out, twice).

| Provider slot | Daily budget | ≈ turns/day | Per-minute ceiling |
| --- | --- | --- | --- |
| Groq `gpt-oss-120b` | 200K TPD | ~28 | 8K TPM → **~1 turn/min** |
| Groq `qwen3.8-27b` | 200K TPD | ~28 | ~1 turn/min |
| Groq `gpt-oss-20b` | 200K TPD | ~28 | ~1 turn/min |
| Cloudflare `gpt-oss-20b` | 10K neurons (~68 neurons per call at 3K in/0.5K out) | ~70 | 300 RPM |
| OpenRouter `:free` | 50 RPD | ~25 | 20 RPM |
| **Total free** | | **~180 turns/day** | |
| Local Ollama fallback | CPU-bound | unbounded, slow | 1 concurrent |

Implications that shape the code work in §6:

- **TPM, not RPD, is the binding constraint on Groq.** 8K TPM means two users chatting at once
  will 429 each other. The router must know token budgets *before* sending, or it will burn
  requests discovering them.
- **Context size is now a cost.** Retrieval top-k and history length directly divide daily turns,
  so context-builder budgets should become per-provider rather than global.
- This is a household/demo budget. Beyond ~100 active turns/day, the next step is paid
  per-token on the same providers (a config change: same base URL, a paid key) or the GPU Space
  already documented in `docs/deployment/inference-plane.md`.

## 5. Step 0 — the decision (ADR-030), before any code

Write `docs/architecture/030-hosted-open-weight-inference.md`. It must decide, explicitly:

1. **A third host category, `HOSTED_OPEN_WEIGHT_HOSTS`**, distinct from both lists that exist:
   - `EXTERNAL_INFERENCE_HOSTS` (banned, no override) keeps closed-model vendors: OpenAI,
     Anthropic, Google Generative Language/Vertex, Azure OpenAI, Bedrock, Cohere, Mistral
     (training opt-in), Perplexity.
   - **Moved out of the banned list into the new gated list:** `api.groq.com`, `openrouter.ai`,
     `api.cloudflare.com` (Workers AI path only), `router.huggingface.co`,
     `integrate.api.nvidia.com` (+ any other the ADR approves, one by one, each with its data
     terms recorded).
   - Gated by a **new, separate** flag `AURA_ALLOW_HOSTED_INFERENCE=true` — not the existing
     `AURA_ALLOW_REMOTE_INFERENCE`, because "my weights on rented hardware" and "someone else's
     server sees my prompts" are different consents, and conflating them would make one silently
     imply the other.
2. **Open-weight only, enforced, not documented.** OpenRouter and Google serve closed models on
   the same key. A provider on a mixed-catalog host must declare a model ID matching an
   open-weight allowlist (`openai/gpt-oss-*`, `qwen/*`, `meta-llama/*`, `google/gemma-*`,
   `@cf/*`, …) or boot fails.
3. **No-training providers only.** A provider whose free tier trains on inputs (Mistral
   Experiment, Google AI Studio free) is not admitted regardless of the flag.
4. **Data-sensitivity routing.** Which content may leave: see §6.4. Default: Engineer-workspace
   turns and any tenant marked `sensitive` stay local.
5. **Which CLAUDE.md / README / developer-guide statements change.** "Self-hosting is a
   fitness-tested invariant" becomes "self-hosted by default; hosted open-weight inference is a
   gated, logged opt-in (ADR-030)". The gap register and `AURA_MASTER_UPGRADE_ROADMAP.md` get an
   entry rather than a silent rewrite.

If the ADR is rejected, the fallback that needs **no** policy change is Category B: keep
self-hosting, but run a larger model on rented GPU (the existing HF Space inference plane) —
not free, but private.

## 6. Implementation phases

Each phase lists its exit evidence, per the repo's "built ≠ tested ≠ proven" rule.

### Phase 1 — Policy layer (backend, no behavior change by default)

Files: `aura_core/config/external_inference.py`, `aura_core/config/settings.py`,
`tests/architecture/test_no_external_inference.py`.

- Add `HOSTED_OPEN_WEIGHT_HOSTS` + `is_hosted_open_weight_host()`; remove those hosts from
  `EXTERNAL_INFERENCE_HOSTS`. As built, Cloudflare is matched by host
  alone. Closed models on its `/ai/v1` path fail the open-weight model check, which makes a path
  match unnecessary. `gateway.ai.cloudflare.com` (AI Gateway, a proxy to closed vendors) was
  added to the banned list (ADR-030 §2, §7).
- Remove `GROQ_API_KEY` / `OPENROUTER_API_KEY` from `BANNED_CREDENTIAL_NAMES` **or** keep them
  banned and standardise on slot-named secrets (`AURA_PROVIDER_HOSTED1_API_KEY`). Prefer the
  latter: it keeps the scanner strict and the env template vendor-neutral.
- `_reject_external_base_url`: third branch mirroring the gated one — refuse without
  `AURA_ALLOW_HOSTED_INFERENCE`; when permitted, log a warning on every boot naming provider,
  host and the consequence ("prompts and retrieved document content are sent to <host>").
- Open-weight model allowlist check for mixed-catalog hosts.
- `ProviderConfig` must reject `is_local=true` on any hosted/remote host (so `LOCAL_FIRST`
  routing can never be fooled).
- Tests (all offline): refused without flag; permitted with flag; closed vendors still refused
  *with* the flag; closed model ID on OpenRouter refused; `is_local` lie refused; the static
  scan still catches `api.openai.com` etc.

Exit: `pytest` + `pytest tests/architecture -q` + legacy `unittest` green.

### Phase 2 — Quota-aware routing (the real engineering)

Free tiers fail by `429`, and today a `429` is just "retryable": the router moves on, the
consecutive-failure breaker eventually opens, then half-open probes **spend more quota** finding
out the day hasn't reset. Needed:

1. **Declared limits as provider data** (ADR-003 style): `AURA_PROVIDER_<NAME>_{RPM,RPD,TPM,TPD}`
   on `ProviderConfig`/`ProviderCapabilities`; all default to "undeclared" so existing configs
   route exactly as before.
2. **Token-bucket pre-check** in `aura_core/llm/admission.py`: estimate request tokens
   (prompt + `max_output_tokens`) and skip a provider that would exceed its TPM/TPD budget *before*
   sending, recording a `quota_skip` routing reason rather than a failure.
3. **Quota cooldown separate from the circuit breaker**: on `429`, read `Retry-After` /
   `x-ratelimit-reset-*` headers (Groq and OpenRouter send them) and bench the provider until
   reset. A daily-quota `429` must not count as a health failure, or a quota day looks like an
   outage on dashboards.
4. **Usage from responses**: `stream_options.include_usage` is already requested
   (`openai_compatible.py:109`) — feed actual usage back into the buckets so estimates self-correct.
5. **Persist counters in Redis** (in-memory adapter for tests), so a gateway restart doesn't
   forget it already spent today's quota.
6. **Health probes must stay free**: `/models` is fine on Groq/OpenRouter; verify Cloudflare's
   `/ai/v1/models` exists, else add `AURA_PROVIDER_<NAME>_HEALTH_PATH`. Never health-check with a
   completion.

Exit: unit tests with `FrozenClock` covering bucket refill, `Retry-After` cooldown, daily reset,
failover order; nothing in the default suite touches the network.

### Phase 3 — Per-provider capability truthfulness

- `supports_json_mode`, `supports_tools`, `context_window`, `max_output_tokens` declared per
  provider from each model card; the planner's `json_schema` response format
  (`openai_compatible.py:118`) must only be sent to providers that honour it, else the planner gets
  prose and fails parsing.
- Re-size `PlanningEngine.plan()`'s 700-token / 180s budget **per provider** rather than globally:
  hosted providers can take a larger plan in a 30–60s timeout; the local fallback keeps the
  current sizing (CLAUDE.md's throughput note still governs it).
- Reasoning models (gpt-oss, Qwen3) emit reasoning tokens that count against TPM — set
  `reasoning_effort: low` (via a per-provider extra-body setting) or budget for it.
- Recorded-response contract tests per provider (fixtures captured once by hand, replayed offline):
  streaming SSE shape, `finish_reason`, usage block, `429` body + headers, tool-call frames.

Exit: contract tests green; a manual capture script exists to refresh fixtures.

### Phase 4 — Data-sensitivity routing

Hosted inference changes who sees AURA's DATA channel. Minimum controls:

- A request-level `data_classification` (`public` / `internal` / `sensitive`) derived from the
  caller context (ADR-029) and the content's provenance: Engineer-workspace file content, uploaded
  documents marked private, and memory flagged personal → `sensitive`.
- Router filter: `sensitive` requests consider only `is_local` providers. If none is healthy, fail
  with a clear error rather than silently sending it out.
- Per-tenant/admin toggle "allow hosted models" surfaced in `aura_admin/`, default **off**.
- Audit log line per hosted call: provider, model, token counts, classification — never content.

Exit: tests proving a `sensitive` turn never reaches a hosted provider even when it is the only
healthy one.

### Phase 5 — Deployment wiring

- `deploy/.env.example`: a commented "free hosted open-weight" block (slot-named providers,
  `AURA_ALLOW_HOSTED_INFERENCE`, `AURA_LLM_ROUTING_POLICY=quality_first`, keys as file-backed
  secrets like the HF Space one).
- `deploy/docker-compose.prod.yml` **and** `docker-compose.dev.yml` in the same commit (parity
  rule): env passthrough for the new slots; the gateway needs egress to the approved hosts —
  verify `gateway`'s Docker network has outbound access and that the deployment's egress policy
  names exactly the approved hosts.
- Keep Ollama, but consider a smaller resident model now that it is only the fallback (frees VPS
  RAM for Postgres/Qdrant).
- Railway control-plane variant (`docs/deployment/control-plane.md`): same env block.
- Secrets: GitHub Actions / VPS secret files for the 2–4 provider keys; `deploy-backend.yml`
  untouched otherwise.

Exit: `docker compose config` renders both files; fitness tests pass on the new templates.

### Phase 6 — Proof on the VPS

Per the repo's standing rule, Phases 1–5 being green proves nothing about live behaviour.

- `deploy/scripts/verify-hosted-inference.sh`: drive N real chat turns through the gateway's own
  wiring; record per-provider latency (TTFT, tok/s), 429 count, quota-skip count, fallback-to-local
  count, and that a `sensitive` probe turn was served locally. Write the report to
  `docs/reports/`.
- Run once at low load and once deliberately past Groq's TPM to show the cooldown + failover path.
- Only then flip `AURA_LLM_PROVIDERS` in production, and add a gap-register entry with the
  report as evidence.

## 7. Rollout order and rollback

1. ADR-030 accepted → Phase 1 merged (no runtime change; flag off).
2. Phases 2–4 merged (still flag off; local-only behaviour identical).
3. Dev stack (`aura-dev`) flipped on with real keys → Phase 6 report.
4. Prod flipped: `AURA_LLM_PROVIDERS=groq_a,groq_b,cfai,orfree,local`,
   `AURA_LLM_ROUTING_POLICY=quality_first`, `AURA_ALLOW_HOSTED_INFERENCE=true`.

**Rollback is one env change:** `AURA_LLM_PROVIDERS=local` (or unset the flag — boot then refuses
the hosted providers loudly). No migration, no client release, no data to move.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Free model withdrawn / tier terms change (happened 3× in the last 4 months: Cerebras, GitHub Models, Groq Llama) | Multiple providers + local fallback; model IDs in env not code; monthly re-verification of §2. |
| Quota exhaustion mid-conversation | Pre-send budget check; failover only before first token (existing router contract); local fallback. |
| Private documents/memory sent to a third party | Phase 4 classification routing; per-tenant opt-in default off; providers that train on inputs excluded. |
| Provider ToS forbids production/commercial use of free tier | Check each ToS in the ADR; treat free tier as dev/small-scale only. |
| Prompt-injection surface unchanged | Trust channels / quarantine (ADR-006) run in the kernel before any provider — unaffected by where the model is. |
| Weakening a guardrail sets precedent | New category is opt-in, logged every boot, per-host allowlisted, closed vendors stay banned with no override. |

## 9. Sources

- OpenRouter, *Free LLM APIs Compared* (updated 2026-09-24): https://openrouter.ai/blog/tutorials/free-llm-apis-compared/
- Free-tier tracker, verified 2026-10-04/10: https://gravity.fast/data/free-llm-api-tiers/
- Groq rate limits: https://console.groq.com/docs/rate-limits · data controls: https://console.groq.com/docs/your-data
- Cloudflare Workers AI pricing: https://developers.cloudflare.com/workers-ai/platform/pricing/ · OpenAI compatibility: https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility
- Google Gemini OpenAI compatibility: https://ai.google.dev/gemini-api/docs/openai
- Hugging Face Inference Providers pricing: https://huggingface.co/docs/inference-providers/pricing
- Mistral Experiment plan: https://help.mistral.ai/en/articles/455206-how-can-i-try-the-api-for-free-with-the-experiment-plan
- NVIDIA API rate-limit reports: https://forums.developer.nvidia.com/t/request-for-nvidia-build-api-rate-limit-increase-40-rpm-200-rpm/378450
