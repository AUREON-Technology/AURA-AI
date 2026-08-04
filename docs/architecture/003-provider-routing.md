# ADR-003 — OpenAI-compatible protocol as the local-inference contract, policy-driven routing

**Status:** Accepted · **Date:** 2026-08-02

## Context

Today Gemini 2.5 Flash is hard-coded in four modules and Ollama is reachable only as a bare-`except`
fallback that yields a "connection failed" line into the user's stream. There is no way to choose a
model per request, no cost control, no latency control, and no health-aware failover.

The platform must support local open-weight models on the VPS *and* multiple cloud providers, and
must let the operator change that mix without a code deploy.

## Decision

**1. One port.** `ports/llm.py` defines `LLMProvider` with `complete()`, `stream()`, `embed()`,
`health()`, and a `capabilities` descriptor (`supports_tools`, `supports_streaming`,
`supports_json_mode`, `context_window`, `cost_per_1k_input/output`, `is_local`).

**2. OpenAI-compatible is the local contract.** vLLM, Ollama (`/v1`), text-generation-inference, and
`llama.cpp`'s server all expose an OpenAI-compatible `/v1/chat/completions`. The
`OpenAICompatibleProvider` adapter therefore covers *all* local serving options and most cloud ones
with a single, well-tested implementation. Vendor-specific adapters exist only where the wire format
genuinely differs (Gemini, Anthropic) and for Ollama's native API (needed for model lifecycle
operations that the OpenAI surface does not expose).

**3. Routing is policy, not code.** `llm/router.py` selects a provider per request from declarative
policy:

```
route(request) →
  candidates = providers matching required capabilities and model class
  filter     = healthy (circuit breaker closed) and under concurrency limit
  order by   = policy: LOCAL_FIRST | CHEAPEST | FASTEST | QUALITY_FIRST | BALANCED
  attempt    = in order, with per-attempt timeout, until one succeeds
```

Failover only occurs **before the first token is emitted**. Once streaming has begun, a mid-stream
failure surfaces as an error event rather than silently restarting with a different provider and
duplicating text (a real defect in the current code — `services/agent.py:308` retries after tokens
have already been sent).

**4. Health and circuit breaking.** Each provider has a circuit breaker (consecutive-failure
threshold, open duration, half-open probe). An open breaker removes the provider from candidacy and
raises `aura_llm_provider_unhealthy`.

**5. Load balancing.** Within a provider *pool* (N vLLM nodes), selection is weighted least-inflight
with EWMA latency as the tiebreak.

**6. Caching.** A two-tier cache in front of routing: exact (hash of normalised messages + model +
sampling params) and semantic (embedding cosine ≥ threshold, only for `temperature == 0` requests —
caching a sampled generation would make non-determinism inconsistent rather than absent).

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| LangChain / LlamaIndex as the abstraction | Large dependency surface, frequent breaking changes, and their abstractions leak vendor concepts anyway. The port needed here is ~60 lines. For a system that must be auditable and stable on a VPS for years, owning it wins. |
| One provider, swapped by config | No failover, no cost optimisation, no local/cloud split by sensitivity. |
| Route by model name only | Cannot express "prefer local for private data, cloud for hard questions", which is the actual product requirement. |
| Retry across providers mid-stream | Produces duplicated or contradictory partial output. Explicitly excluded. |

## Consequences

**Positive** — provider changes are configuration; a dead provider degrades latency, not
availability; cost policy is enforceable and measurable; local-first routing keeps sensitive prompts
on the operator's hardware.

**Negative** — a translation layer per non-compatible vendor, and capability descriptors must be kept
accurate (a wrong `context_window` causes truncation surprises). Mitigated by a contract test suite
(`tests/unit/llm/test_provider_contract.py`) that every adapter must pass.

**Impact**

| Dimension | Effect |
| --- | --- |
| Scalability | Enables an inference pool behind one port; the binding constraint (generation concurrency) becomes horizontally scalable. |
| Maintainability | Vendor churn is contained to one file per vendor. |
| Security | Local-first policy keeps regulated data off third-party APIs; per-provider allowlisting. |
| Cost | Directly controllable: cache hits, local overflow, and cheapest-capable routing are the three biggest levers on the bill. |
