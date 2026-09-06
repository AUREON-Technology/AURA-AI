# Ollama vs vLLM: Comparison, Current Usage, and Hybrid Plan

**Status:** analysis + recommendation, no application code changes.
**Scope:** self-hosted LLM inference engines used by `aura_core/llm/` (ADR-003 provider
routing). Answers three questions: how do Ollama and vLLM compare in general, how does AURA-AI
use each one today, and what's the right setup — including a hybrid — for a CPU-only deployment.

## 1. Ollama vs vLLM, general comparison

| | **Ollama** | **vLLM** |
|---|---|---|
| Designed for | Easy local/single-user serving; wraps `llama.cpp` | High-throughput multi-user GPU serving |
| Quantization | GGUF (Q4/Q5/Q8) — mature, small footprint | AWQ/GPTQ/FP8, partial GGUF support — heavier |
| CPU inference | Mature, primary path (`llama.cpp` backend) | Exists but second-class: needs AVX-512 for decent perf, larger KV-cache RAM footprint |
| GPU inference | Works, but no continuous batching / PagedAttention | Its core reason to exist — near-linear throughput scaling with concurrent requests on GPU |
| Model management | Built-in (`pull`/`create`/`delete`, Modelfile) | None — points at a weights directory you manage |
| API surface | Native `/api/*` + OpenAI-compatible `/v1/*` | OpenAI-compatible `/v1/*` only |
| Setup complexity | Low — single binary/container | Higher — CUDA, driver, toolkit, larger image |
| Best fit | CPU boxes, low concurrency, single-box self-hosting | GPU boxes serving many concurrent users |

**Takeaway:** on CPU, Ollama is the better-supported, better-optimized engine by a wide margin.
vLLM's defining advantages — continuous batching and PagedAttention — only activate with a GPU
and concurrent request volume large enough to batch.

## 2. Current usage in AURA-AI (verified against this repo)

- **Ollama is real and deployed.** `deploy/docker-compose.prod.yml:398-429` runs
  `ollama/ollama:latest` (digest-pinned), CPU-only by default — the NVIDIA GPU device
  reservation is present but commented out (lines 427-429). Configured with
  `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, limited to 6 of 8 cores / 10G RAM.
  Default model is `qwen2.5:3b-instruct-q4_K_M` (`deploy/.env.example:45-46`).
- **vLLM has zero deployment footprint.** No compose service, no Terraform resource, no env var
  anywhere in the repo. It exists only as a theoretically-supported `openai_compatible` backend
  in `aura_core/llm/registry.py` (ADR-003, `docs/architecture/003-provider-routing.md`). The
  project's own roadmap and readiness docs say this plainly: *"vLLM and llama.cpp are supported
  by protocol, not by having been tried"* (`docs/AURA_UPGRADE_ROADMAP.md`,
  `docs/reports/AURA_AI_PRODUCTION_READINESS_SCORE.md:33`). Only Ollama (plus the deterministic
  `echo` test provider) has ever run in a real test in this codebase.
- **A config quirk worth flagging separately:** the shipped default config reaches Ollama via
  the `openai_compatible` provider kind (`AURA_PROVIDER_LOCAL_BASE_URL=http://ollama:11434/v1`),
  not the dedicated `ollama` kind — because `kind` defaults to `openai_compatible` when unset
  (`aura_core/config/settings.py:287`). The `ollama`-kind `OllamaProvider` (needed for admin
  model-lifecycle operations: pull/delete/create) is never instantiated in the default
  deployment, so `container._ollama_provider()` returns `None` and admin model-management
  endpoints fail with *"no local Ollama provider is configured"*
  (`aura_core/api/admin.py:572,589`) unless a second provider with `kind=ollama` is added
  explicitly.
- **Hardware reality check.** The documented target is 4 vCPU / 16 GB RAM, but
  `docs/reports/AI_HARDWARE_CAPACITY_REPORT.md` audited the actual production VPS at
  **1 vCPU / 3.8 GiB** — under-provisioned even for Ollama alone ("cannot run the platform's
  inference tier" as currently spec'd). Whatever the real box turns out to be, this is the
  binding constraint on model size and concurrency, not the choice of engine.
- **Routing already supports multi-provider hybrids.** `ProviderRouter`
  (`aura_core/llm/router.py`) implements five policies — `LOCAL_FIRST`, `CHEAPEST`, `FASTEST`,
  `QUALITY_FIRST`, `BALANCED` — with per-provider circuit breakers and pre-first-token-only
  failover, over any number of declared providers. The deployed policy is `local_first`
  (`deploy/.env.example:52`, overriding the code-level default of `balanced`). This machinery
  was proven for real with two *simultaneous Ollama* providers of different sizes in
  `tests/load/test_multi_model_routing.py` (Phase G2 of the roadmap) — never against a real
  vLLM node.

## 3. Which engine is better for a CPU-only system

**Ollama, unambiguously, on the CPU-only hardware this project targets and actually runs.** It's
what's built, deployed, and the only engine ever proven under real load in this repo (Phase A4:
p95 latency 70.5s on a 3-core sandbox, concurrency flat from 1 to 8 requests because
`OLLAMA_NUM_PARALLEL=1` serializes generation). vLLM's batching advantage cannot activate under
that kind of single-concurrency CPU serving, its CPU backend is less mature than `llama.cpp`,
and running it alongside Ollama would compete for RAM/CPU that the hardware audit already shows
is the actual bottleneck.

**Recommendation: do not deploy vLLM on the current CPU-only host.**

## 4. Hybrid options

### Option A — same-box hybrid, no new hardware, available today

Run two Ollama-backed providers at different sizes/quality tiers (e.g. a small fast model for
latency-sensitive turns, a 7B model for quality-sensitive turns), routed via
`FASTEST`/`QUALITY_FIRST`/`BALANCED`. This is exactly what Phase G2 already proved for real
(`tests/load/test_multi_model_routing.py`, using `smollm2:135m` + `qwen2.5:3b-instruct-q4_K_M`).

Caveat: the deployed `OLLAMA_MAX_LOADED_MODELS=1` means only one model stays resident at a time.
Running two concurrently means raising that limit and having RAM headroom the currently audited
box doesn't have — this option is architecturally free but not resource-free.

### Option B — genuine Ollama+vLLM hybrid, requires a GPU host

Keep CPU Ollama as the always-on, zero-cost, offline-capable default tier; add vLLM on a GPU box
(owned or rented) as a second provider for quality-sensitive or high-throughput turns. **No
`aura_core` code changes are needed** — `registry.py`/`router.py` already support N providers
with per-provider quality tiers, and this exact pattern is pre-documented as a commented-out
example in `deploy/.env.example:58-70`.

Steps, if/when a GPU host becomes available:

1. Provision a GPU host (own network or rented) with NVIDIA drivers + container toolkit —
   outside this repo's scope.
2. Deploy `vllm/vllm-openai` there, serving a larger model (e.g. `qwen2.5:32b-instruct`) over
   `/v1/chat/completions`.
3. In the real `deploy/.env` (not the example), add a second provider following the pattern
   already sketched at `deploy/.env.example:58-66`:
   ```
   AURA_LLM_PROVIDERS=local,large
   AURA_PROVIDER_LARGE_KIND=openai_compatible
   AURA_PROVIDER_LARGE_MODEL=<model served by vLLM>
   AURA_PROVIDER_LARGE_BASE_URL=http://<gpu-host>:8000/v1
   AURA_PROVIDER_LARGE_QUALITY_TIER=3
   ```
   Add `AURA_ALLOW_REMOTE_INFERENCE=true` only if the GPU host is rented/off-network compute
   (per the README's remote-inference gate); not needed for a GPU box on your own network.
4. Only touch `deploy/docker-compose.prod.yml` if the GPU host should be co-located in the same
   compose stack — add a `vllm` service mirroring the NVIDIA device-reservation pattern already
   present (commented out) on the `ollama` service, lines 427-429. If it's a separate host, no
   compose change is needed; just point `AURA_PROVIDER_LARGE_BASE_URL` off-box.
5. Decide the routing policy: keep `local_first` if Ollama should always be tried first
   (cheap/fast/offline-safe), or move to `balanced`/`quality_first` if the GPU-backed vLLM tier
   should be preferred whenever a turn can tolerate its higher latency/cost in exchange for
   quality.
6. **Validate before trusting it in production** — this closes the exact gap the roadmap flags
   (vLLM "supported by protocol, not tried"): run a health probe and a few real routed
   completions against the new `large` provider, the same way Phase G2 proved multi-Ollama
   routing, before relying on it for real traffic.

No changes to `aura_core/llm/registry.py` or `router.py` are required for either option — this
is a deployment/configuration task, not a code change.

## 5. Verification

- No application code was modified as part of this analysis, so `pytest` and
  `pytest tests/architecture` are unaffected.
- **Option A:** re-run the existing opt-in `tests/load/test_multi_model_routing.py`
  (`AURA_ROUTING_TEST_*` env vars) as the regression check.
- **Option B:** verification is operational once a GPU host exists — a compose/service
  healthcheck on the new `vllm` service, `GET /v1/models` against it directly, and a routed
  request through the running AURA gateway confirming the `large` provider gets selected for a
  quality-tier request (via trace/logs, not a new automated test).
