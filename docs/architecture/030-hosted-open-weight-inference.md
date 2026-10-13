# ADR-030: Hosted open-weight inference as a gated third category

**Status:** Proposed. Phase 1 (policy layer) is built and tested, but not deployed and not used:
the flag defaults to off and no compose file passes it through yet.
**Date:** 2026-10-11 · **Plan:** `docs/roadmaps/AURA_FREE_HOSTED_INFERENCE_PLAN.md`
**Amends:** the self-hosting invariant (`tests/architecture/test_no_external_inference.py`,
`aura_core/config/external_inference.py`) · **Related:** ADR-003 (provider routing), ADR-006
(trust channels), ADR-029 (caller context)

## Context

AURA's production model is `qwen2.5:3b-instruct-q4_K_M` on CPU Ollama. It manages about 5–10
tokens/s, and a 700-token plan can take minutes (`aura_core/planning/engine.py`). Several
providers now serve much larger **open-weight** models (gpt-oss-120b, Qwen3, Llama 3.3/4,
Gemma 4) for free. Within daily quotas, they do it over the same OpenAI-compatible protocol
`openai_compatible` already speaks. The plan's §2 has the survey.

The platform refused all of them. Until this ADR, an inference host fell into one of three cases:

| Category | Example | Policy |
| --- | --- | --- |
| Your hardware | `http://ollama:11434` | Allowed |
| Your weights on rented hardware | `*.hf.space`, Cloud Run | Gated by `AURA_ALLOW_REMOTE_INFERENCE` |
| Anything on `EXTERNAL_INFERENCE_HOSTS` | OpenAI, **Groq, OpenRouter** | Banned, no override |

That table treats "a closed vendor's proprietary model" and "a public open-weight model served
by someone else" as the same thing. They share one property: a third party sees the prompt. They
differ on another: an open-weight model can be self-hosted later with identical behaviour, so
using it creates no vendor lock-in and no dependence on a model we could never run. The question
was whether the shared property alone justifies a ban with no override.

## Decision

### 1. A third category, `HOSTED_OPEN_WEIGHT_HOSTS`, gated by its own flag

`aura_core/config/external_inference.py` gains `HOSTED_OPEN_WEIGHT_HOSTS`:

- `api.groq.com`: does not train on customer data.
- `api.cloudflare.com`: Workers AI.
- `openrouter.ai`
- `router.huggingface.co`: HF Inference Providers.
- `integrate.api.nvidia.com`

The list records each host's data terms beside its entry. `api.groq.com` and `openrouter.ai`
move **off** the banned list.

`ProviderConfig._reject_external_base_url` refuses these hosts unless
`AURA_ALLOW_HOSTED_INFERENCE=true`. The refusal message states what the operator accepts by
setting it:

- every prompt, retrieved passage and memory snippet becomes readable by the provider;
- the system stops working without internet.

When the flag permits a hosted provider, a warning is logged on every boot.

The flag is **separate** from `AURA_ALLOW_REMOTE_INFERENCE`, and neither flag implies the other
(both directions are tested). "My weights may run on rented hardware" and "a third party may read
my prompts" are different consents. With one shared flag, granting the first would silently grant
the second.

### 2. Open weights only, enforced by model ID

Several of these keys also reach closed models. OpenRouter serves GPT-5 and Claude. Workers AI's
`/ai/v1` routes `openai/gpt-4.1`. Hugging Face routes to closed partners. A host allowlist alone
would therefore not mean "open weights".

For a hosted provider, `is_open_weight_model()` must accept the model ID or boot fails. It
normalises the ID first: lowercase, strip the `@cf/` / `@hf/` prefix, drop the `:free` /
`:<provider>` suffix. It then requires two things:

- **an anchored publisher+family match.** `openai/gpt-oss-*` passes and `openai/gpt-5` does not;
  `google/gemma-*` passes and `google/gemini-*` does not. An unknown publisher matches nothing.
- **no closed-tier word** (`max`, `plus`, `turbo`). Qwen's `qwen-max` and `qwen3-max` share an
  open family's name but have no released weights.

The check errs toward refusing. Admitting a new family is a reviewed edit to
`OPEN_WEIGHT_MODEL_PATTERNS`.

### 3. Providers that train on inputs are not admitted

A host is listed only if its default or free tier does not train on API inputs. That excludes
Mistral's Experiment plan and Google AI Studio's free tier, whose hosts stay banned. OpenRouter
is admitted on the condition that the operator keeps prompt training **off** in its privacy
settings. Free endpoints that require training then refuse the request upstream.

### 4. A hosted provider is never `is_local`

`is_local=true` on a hosted host fails at boot. `is_local` steers `local_first` routing, and
Phase 4 will use it for sensitive-data routing. A mislabelled hosted provider would defeat both.

### 5. Hosted endpoints are configuration, never code

Moving hosts off the banned list also removes them from fitness layer 2's source scan. A new test
fails if any `HOSTED_OPEN_WEIGHT_HOSTS` entry appears in Python source outside the registry. A
hosted endpoint must reach the process only through `AURA_PROVIDER_<NAME>_BASE_URL`.

### 6. Credentials stay vendor-neutral

`BANNED_CREDENTIAL_NAMES` is unchanged, so `GROQ_API_KEY` and `OPENROUTER_API_KEY` still fail
the manifest scan. Hosted keys use the existing slot convention, with
`AURA_PROVIDER_<SLOT>_API_KEY_ENV` pointing at e.g. `AURA_PROVIDER_HOSTED1_API_KEY`. This keeps
the scanner strict and the env templates free of vendor names.

### 7. Closing a proxy route found while doing this

`gateway.ai.cloudflare.com` is added to `EXTERNAL_INFERENCE_HOSTS`. Cloudflare's AI Gateway
forwards to OpenAI, Anthropic and others under one URL shape. Allowing Workers AI without
banning it would have opened a route to closed vendors.

## What stays the same

- The banned list keeps every closed-model vendor, and neither flag unlocks any of them.
- No vendor SDK, no new provider `kind`, no Flutter-side change. `ProviderConfig._KINDS` is still
  exactly `{openai_compatible, ollama, echo}`.
- Embeddings, ASR, TTS, OCR and reranking stay local. This ADR covers chat/planning generation
  only.
- With the flag unset, behaviour is identical to before. That is the state of every deployment
  today, because no compose file passes `AURA_ALLOW_HOSTED_INFERENCE` through yet. That is
  Phase 5, and it must land in `docker-compose.prod.yml` and `docker-compose.dev.yml` together.

## Consequences

**Gains**

- 20–120B models at hundreds of tokens/s for $0 within quota, as a config change.
- Because the models are open-weight, any of them can later move to the existing GPU Space or a
  self-run vLLM with no behaviour change.

**Costs**

- Prompts, retrieved document content and memory leave infrastructure we control for every turn
  routed to a hosted provider. Until Phase 4's sensitivity routing exists, **every** turn is
  eligible once the flag is on. Operators should enable it only where that is acceptable.
- Free quotas are small (≈180 turns/day across all providers, plan §4), and free models are
  withdrawn without notice. Hosted-only deployments are discouraged; keep the local provider as
  the last candidate.
- The self-hosting claim in `README.md` becomes "self-hosted by default". A deployment with the
  flag on is not self-hosted, and its boot log says so.
- The open-weight pattern list needs upkeep as families appear. A missing family fails closed.

## Rejected alternatives

- **Remove the ban outright.** This loses the distinction between open and closed models, and
  between consenting and not consenting.
- **Reuse `AURA_ALLOW_REMOTE_INFERENCE`.** Rejected because it conflates two consents (§1).
- **Self-run proxy on a permitted host.** This hides the egress from the in-process checks.
  `external_inference.py` already names this as the case they cannot catch. Doing it on purpose
  would make the policy fiction.
- **Host allowlist without a model check.** Rejected because it reaches closed models through
  mixed catalogs (§2).

## Follow-ups (plan §6)

- **Phase 2:** quota-aware routing (declared RPM/TPM/RPD/TPD, pre-send budget check, a
  `Retry-After` cooldown separate from the circuit breaker). **Built and tested, not proven
  live:** `aura_core/llm/quota.py`, wired through `ProviderRouter` and the container.
- **Phase 3:** per-provider capability flags and planner sizing.
- **Phase 4:** data-sensitivity routing. This must land before any deployment where private
  documents are in scope.
- **Phase 5:** deploy wiring.
- **Phase 6:** a VPS evidence report. The phase is not proven until that report exists.
- Consider also refusing `is_local=true` on `REMOTE_INFERENCE_HOSTS`. That is left out here
  because it could break an existing private deployment config without warning.
