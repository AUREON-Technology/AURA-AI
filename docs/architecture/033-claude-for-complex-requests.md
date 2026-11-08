# ADR-033 — Claude as a gated provider for complex requests

**Status:** Proposed (built, not proven) · **Date:** 2026-11-08 · **Related:** ADR-003
(routing), ADR-006 (trust channels), ADR-030 (hosted inference and data classification)

## Context

The self-hosting rule says no inference leaves our infrastructure. ADR-030 already made one
exception, for hosted APIs that serve *open-weight* models, and kept it narrow: an opt-in flag,
open-weight model IDs only, never `is_local`, an `internal` data ceiling by default. Closed-model
vendors stayed banned under every flag.

The owner asked for the opposite of that last line. They want Anthropic's Claude added as a
provider, and they want the system to send it the most complex requests. The owner made that
call knowing it reverses part of the rule. This ADR records the reversal and keeps it as narrow
as the static scans and the router allow.

The CPU-served local model (`qwen2.5:3b`, about 5–10 tokens/s in production) is weakest on
exactly those turns: multi-part design questions, proofs, debugging with code, long plans.
Nothing routed by difficulty yet. The router could already prefer a pool or a quality tier, but
nothing set either one from the request itself.

## Decision

### 1. One vendor, one kind, one module

- **A new provider kind, `claude`.** It is not `anthropic`: that name stays in
  `BANNED_PROVIDER_KINDS` for the adapter that was removed. `ProviderConfig._gate_claude`
  refuses the kind unless `AURA_ALLOW_CLAUDE_INFERENCE=true`. `AURA_ALLOW_HOSTED_INFERENCE` does
  not cover it, and this flag covers no other vendor.
- **The endpoint cannot be redirected.** A `claude` provider must not set `base_url`; it uses
  the SDK's own endpoint. `api.anthropic.com` stays in `EXTERNAL_INFERENCE_HOSTS`, so no
  `base_url` of any kind may name it. That also blocks `openai_compatible` against Anthropic's
  compatibility endpoint.
- **The key comes only from the slot's variable.** `AURA_PROVIDER_<NAME>_API_KEY_ENV` is
  required, and the adapter passes the key explicitly. An empty key is refused, never resolved
  ambiently. `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` stay banned in deployment manifests,
  so the SDK's ambient lookup has nothing to find.
- **The model must be a Claude model ID** (`claude-…`, as Anthropic's API names it), and the
  provider is never `is_local`.
- **The SDK stays banned everywhere but one file.** `anthropic` stays in `BANNED_SDK_ROOTS`.
  `SANCTIONED_SDK_IMPORTERS` lets exactly `aura_core/llm/providers/claude.py` import it, and
  only lazily. `anthropic` is the one sanctioned distribution; `langchain-anthropic` stays banned.

`TestClaudeIsGatedNotSilent` pins every one of these edges, so widening the exception fails a
named test.

### 2. Third-party semantics from ADR-030, unchanged

`ProviderConfig.third_party` is true for this kind. That gives the provider the following, with
no new code in the router:

- an **`internal` data ceiling** by default: a turn whose context includes documents, memory,
  goals, tool output, Engineer workspaces or caller evidence is `sensitive` and is answered
  locally (`MAX_DATA_CLASSIFICATION=sensitive` raises it, with a boot warning);
- **no user images**, whatever the ceiling (`third_party_image`);
- an **audit record** for every generation it serves (`llm.third_party_generation`).

### 3. Complexity routing as a pool preference

- `aura_core/llm/complexity.py` scores the user's own message without a model call. It is a
  bounded, weighted sum of independent signals: reasoning terms in English and Arabic, formal
  terms (prove, derive), stated constraints, multi-part lists, several questions, code blocks
  and maths. The score is deterministic and the signals can be explained.
- With `AURA_LLM_COMPLEXITY_ROUTING=true`, an *interactive* turn whose score meets
  `AURA_LLM_COMPLEXITY_THRESHOLD` (default 0.5) asks for the `reasoning` pool instead of
  `general-fast`. Voice, Engineer and background turns keep their pools, and so does any caller
  that set `workload` explicitly.
- The Claude provider declares `POOLS=reasoning`. Nothing else changes in the router:
  - pool members are tried first;
  - generalists are the fallback;
  - every capability, classification, circuit and quota check still applies.

  So complex turns go to Claude first and fall back to the local model when Claude is down,
  rate-limited or barred by the data ceiling. Everyday turns never reach Claude: a provider
  that declares only `reasoning` is `pool_mismatch` for `general-fast`.

Both switches are off by default, so a deployment that sets neither routes exactly as before.

### 4. Adapter choices

- The adapter uses the official SDK with `max_retries=0`: the router owns retry and failover.
- Every call streams, including `complete`.
- It sends no sampling parameters, which current models reject. Quality is steered with
  `output_config.effort`: `high` by default, overridable via `EXTRA_BODY`.
- Each request asks for 8000 tokens of thinking headroom on top of AURA's answer budget, capped
  at the declared `MAX_OUTPUT_TOKENS`. Without it, adaptive thinking could spend a budget sized
  for a CPU model and return an empty answer.
- Prompt caching is on.
- Server-side refusal fallback is on (`fallbacks: "default"`). `EXTRA_BODY='{"fallbacks": null}'`
  turns it off.

## Consequences

- **Complex turns leave our infrastructure when enabled.** The user's message and any untainted
  conversation go to Anthropic. These turns depend on the internet and on an Anthropic account
  with billing.
- **Cost becomes real.** Opus-tier pricing is $4 / $20 per million input/output tokens. Declare
  `COST_INPUT=0.004` / `COST_OUTPUT=0.02` so the router and cost telemetry see it. The heuristic
  errs toward cheap failures: a missed complex message is answered locally, as before, and a
  false positive costs one hosted call.
- **The self-hosting fitness tests now encode an exception.** They still catch any other vendor,
  any second importer, and any redirect of this one.
- **Context is still capped by `AURA_LLM_MAX_PROMPT_TOKENS`.** That cap was sized for the CPU
  model and applies before routing. Raising it for complex turns alone would have to survive a
  fallback to that model, so it is left for a follow-up.

## Proof

Built and tested with no network:

- `tests/unit/test_complexity_routing.py` drives the real `anthropic` SDK over an in-process
  transport and checks the request shape, parsing, errors and the ambient-key refusal. It also
  runs real kernel turns on the in-memory container with echo providers, and covers the
  router's ordering, fallback and data-ceiling behavior.
- `TestClaudeIsGatedNotSilent` pins the gate.

**Not proven:** no call has reached Anthropic's API from a deployed gateway. Proof is one
complex turn on the live target that `/api/v3/chat` attributes to the Claude provider, with the
audit record present, plus one everyday turn that is still answered locally.
