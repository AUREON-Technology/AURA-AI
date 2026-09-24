# Troubleshooting — two-plane deployment

Failure modes to test deliberately before trusting this topology in production (task §18/§28),
and what each one actually looks like given the *existing*, unmodified `ProviderRouter`/circuit
breaker behavior (ADR-003). None of these are new failure paths built for this migration — they
are the pre-existing single-provider behavior, examined honestly now that the sole provider is
a network hop away instead of co-located.

| Failure | What happens today (no code changed) | How to provoke it for a real test |
| --- | --- | --- |
| Inference Space unavailable (DNS/network) | `OpenAICompatibleProvider`'s HTTP client raises a connection error → counted as a provider failure by `router.py` → after `AURA_LLM_CIRCUIT_THRESHOLD` (default 5) consecutive failures, the circuit opens → subsequent requests fail fast with `AllProvidersFailedError` instead of hanging, until `AURA_LLM_CIRCUIT_OPEN_S` (default 30s) elapses and a probe is attempted | Stop the Space, or point `AURA_PROVIDER_HFSPACE_BASE_URL` at an unreachable host, then send chat turns |
| Inference timeout | Request is cancelled at `AURA_PROVIDER_HFSPACE_TIMEOUT_S` (45s default for the GPU variant) — surfaces as a provider failure, same circuit-breaker path as above. Sized for a *warm* GPU; a cold-start Space needs a longer value or an always-on flavor (`inference-plane.md` §3) | Use a sleeping Space flavor and send a request right after a period of inactivity |
| Inference 401/403 | `VLLM_API_KEY` / `AURA_PROVIDER_HFSPACE_API_KEY` mismatch — vLLM's own auth middleware rejects the request before it reaches the model; surfaces as an HTTP error the provider treats as a failure (same breaker path) | Deliberately mismatch the two keys temporarily on a non-production Space |
| Inference 429 | vLLM's own concurrency limit; `AURA_PROVIDER_HFSPACE_MAX_CONCURRENCY` (8 default) is the Control Plane's own admission control meant to stay under it — a 429 anyway means the two are out of sync and is a config bug, not expected steady-state behavior | Raise `AURA_PROVIDER_HFSPACE_MAX_CONCURRENCY` well past what the Space's flavor can actually serve, then load-test |
| Inference 5xx | Treated identically to a connection error by the provider — a provider failure, breaker path as above | Kill the vLLM process inside the Space (via its logs/restart) mid-request |
| Model unavailable / model load failure | vLLM itself refuses to start or its `/v1/models` never reports the configured `VLLM_MODEL` — this is a **build-time/boot-time** failure, not a runtime one the circuit breaker can route around, since there's no other model to fall back to with `hfspace` as sole provider (`inference-plane.md` §2). Diagnose via the Space's own logs, not Control Plane telemetry | Set `VLLM_MODEL` to a nonexistent repo or one that doesn't fit the flavor's VRAM |
| GPU unavailable / out-of-memory | vLLM fails to allocate KV-cache at the configured `VLLM_GPU_MEMORY_UTILIZATION` and exits — again boot-time, visible only in Space logs. There is no CPU silent-fallback: `vllm-openai` does not run without CUDA, so this fails loudly (the base image has no CPU code path), matching the task's own "avoid silently falling back to CPU inference" requirement by construction, not by anything added here | Set `VLLM_GPU_MEMORY_UTILIZATION` near 1.0 with a model too large for the flavor's VRAM |
| Streaming interrupted / connection dropped mid-generation | ADR-003's explicit rule: **no failover after the first token has streamed to the caller.** The client sees a truncated/error response for that one turn; the *next* turn may route elsewhere if the breaker has since opened. This is existing, deliberate behavior — a mid-stream silent failover would mean the client can't tell where the rest of the answer came from | Kill the Space connection partway through a long streamed response |
| Control Plane restart | Stateless process; Postgres/Redis/Qdrant hold all durable state externally (Railway managed services / external Qdrant). In-flight requests to that instance are dropped; new requests route to a replacement instance once Railway's healthcheck passes | Restart the `gateway` service in Railway mid-request |
| Inference Plane restart | Same as "unavailable" above, bounded by the circuit breaker | Restart the Space from its dashboard while sending traffic |
| Both restart independently | No shared failure mode exists between them beyond both being unavailable simultaneously — the breaker's behavior in that case is identical to a single-sided outage, just with no recovery until at least one side is back | Restart both, observe `/health/ready` on the Control Plane throughout |

## What must not happen (verify, don't assume)

- **Hang indefinitely**: bounded by `AURA_PROVIDER_HFSPACE_TIMEOUT_S` and the circuit breaker —
  verify the bound is actually hit in a real test, not just configured.
- **Expose infrastructure credentials or stack traces with secrets**: `AuraSettings.validate()`
  already refuses `debug=true` in production/staging (`aura_core/config/settings.py`), which is
  what keeps FastAPI's debug traceback pages off; this migration doesn't touch that gate.
- **Create duplicate durable side effects on retry**: `ProviderRouter`'s failover boundary
  (before first token only) is what already prevents "answered twice from two providers" —
  unchanged.
- **Mark a failed inference as successful**: any non-2xx or connection failure from the Space is
  a provider failure to `router.py`, never silently swallowed into a synthetic success.

## A note specific to single-provider (primary) mode

With `local` removed (`inference-plane.md` §2), every failure row above that used to be a
*degradation* (fail over to Ollama) is now a *full outage* for generation. That is the real
cost of the "primary, not failover" topology this migration documents as one option — it is not
a defect introduced by the migration, it's the direct consequence of having one inference
provider instead of two. If that risk profile isn't acceptable, keep the `local,hfspace`
failover topology `docs/guides/split-inference-deployment.md` §8 already documents.
