# Operations — two-plane deployment

Day-2 operations for the Railway Control Plane / Hugging Face Inference Plane split. This
doesn't introduce new observability architecture — ADR-009 (observability-first) already covers
structured logging, correlation IDs and metrics; this records what to look at now that a
network hop exists between the two planes.

## 1. Correlation across the plane boundary

Every inbound request already gets a correlation/request ID (ADR-009). `OpenAICompatibleProvider`
does not currently forward that ID as a request header to the Space, and vLLM has no concept of
it either — so the cross-plane trace today is by **timestamp + provider name in the Control
Plane's own logs**, not a shared trace ID stamped on both sides. If tighter cross-plane tracing
is wanted later, that's a small code change to `OpenAICompatibleProvider` (add a header) — not
built here, since the task's own §32 constraint is deployment-only, and inventing a new header
Vllm's stock image doesn't read wouldn't make it visible on the Space side anyway.

What **is** already traceable, per turn, in the Control Plane's own metrics/logs (ADR-009):
`provider` (`hfspace`), `model`, queue time, time-to-first-token (`aura_llm_ttft_seconds`),
generation duration, status, error class. This is what `router.py`'s circuit breaker and
`observability/metrics.py` already emit — unchanged by this migration.

## 2. Health checks — what "healthy" means per plane

**Control Plane** `/health` and `/health/ready` (existing endpoints, unchanged) distinguish:

- application process alive
- Postgres reachable
- Redis reachable
- Qdrant reachable
- inference dependency healthy — this is the `hfspace` provider's circuit-breaker state
  (`router.py`), not a raw ping; a request to `/health` does not itself call the Space (which
  would waste GPU time on every healthcheck). It reports the breaker's last-known state.

Do not point Railway's healthcheck at anything but `/health/ready` — `/health` alone (per
CLAUDE.md's existing convention) may report `ok` while a dependency is down, which is exactly
the "reports the whole system healthy merely because the process is alive" failure §20 warns
against.

**Inference Plane**: vLLM's own `/v1/models` is the only readiness signal that exists (see
`inference-plane.md` §6). It conflates "service started" and "model loaded" — vLLM doesn't
serve that endpoint until the model has finished loading, so a 200 there already implies both.
There is no separate GPU-detected / CUDA-available signal exposed over HTTP; that only appears
in the Space's build/runtime logs (Hugging Face's own Space logs UI).

## 3. Logs

- Control Plane: Railway's own log stream per service (`gateway`, `worker-realtime`,
  `worker-ingestion`, `beat`), already JSON (`AURA_LOG_FORMAT=json`).
- Inference Plane: the Space's **Logs** tab in the Hugging Face UI. vLLM logs model load time,
  served model name, and per-request timing to stdout, which Spaces capture directly — no
  separate log shipping was built for this migration.

## 4. Restart behavior

- **Control Plane restart** (Railway redeploy or crash-restart): stateless — Postgres/Redis/Qdrant
  are external managed services, so a gateway restart loses no durable state. In-flight streamed
  responses to clients are dropped (ADR-003: failover only before the first token; a mid-stream
  provider or process failure surfaces as an error to that one caller, by design).
- **Inference Plane restart** (Space restart, redeploy, or sleep/wake): the Control Plane's
  circuit breaker (`AURA_LLM_CIRCUIT_THRESHOLD`, default 5 consecutive failures) opens after
  repeated failures and stops sending traffic to it; with `hfspace` as the sole provider (§2 of
  `inference-plane.md`), this means generation is unavailable, not degraded, until the breaker's
  next probe (`AURA_LLM_CIRCUIT_OPEN_S`, default 30s) succeeds.
- **Both restart independently** — nothing in this migration couples their lifecycles. A Space
  redeploy doesn't touch Railway; a Railway redeploy doesn't touch the Space.

## 5. What to watch after cutover

Before relying on this topology in production, confirm (see `docs/deployment/troubleshooting.md`
for how to provoke each one deliberately):

- `aura_llm_ttft_seconds` p95 against the existing alert threshold (`AuraSlowTimeToFirstToken`,
  p95 > 8s) — a cross-plane network hop adds latency the co-located VPS/Ollama topology didn't
  have.
- Circuit breaker open/close events in logs during a deliberate Space restart.
- That `/health/ready` actually flips unhealthy when the Space is unreachable, and recovers
  automatically once it's back — no manual intervention should be required for either
  direction.
