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
`aura_core` code changes were needed** — `registry.py`/`router.py` already support N providers
with per-provider quality tiers. The scaffolding for this option is now in the repo, ready to
activate the moment a GPU host exists — see `docs/guides/gpu-hybrid-setup.md` for the full
walkthrough. Summary of what's already in place:

1. **`deploy/docker-compose.prod.yml`** has an optional `vllm` service (image
   `vllm/vllm-openai:latest`, GPU device reservation, `/v1/models` healthcheck, its own named
   volume for the Hugging Face weight cache) gated behind a `gpu` compose profile — it does not
   start with a plain `docker compose up -d`, only with
   `docker compose -f deploy/docker-compose.prod.yml --profile gpu up -d vllm`. `gateway` and
   the Celery workers already pass through `AURA_PROVIDER_LARGE_*` env vars (empty by default,
   so this is a no-op until `AURA_LLM_PROVIDERS` also lists `large`).
2. **`deploy/.env.example`** documents the exact `AURA_PROVIDER_LARGE_*` block to add to the real
   `deploy/.env`, including the `AURA_VLLM_MODEL` / `AURA_VLLM_GPU_MEMORY_UTILIZATION` variables
   that configure the compose service itself.
3. **`tests/load/test_vllm_hybrid_routing.py`** is a new opt-in test, structured exactly like
   Phase G2's `test_multi_model_routing.py`, that pairs a real `OllamaProvider` against a real
   `OpenAICompatibleProvider` (vLLM) and proves health, completion, and `QUALITY_FIRST`
   failover-to-vLLM against genuinely different backends — not two Ollama instances. This is
   what closes the gap the roadmap itself names (vLLM "supported by protocol, not tried"): it
   stays skipped until the four `AURA_HYBRID_TEST_*` env vars point at a real Ollama + a real
   vLLM server, so CI is unaffected.

What still needs the actual hardware (out of this repo's scope): provisioning a GPU host with
NVIDIA drivers + the container toolkit, bringing up the `vllm` profile there, and running the
new opt-in test against it before trusting the `large` provider with real traffic. One subtlety
worth calling out: `AURA_PROVIDER_LARGE_IS_LOCAL` defaults to `true`, not `false` — `is_local`
means "your own infrastructure, not a vendor" (ADR-003), and a GPU box you own qualifies just as
much as the CPU Ollama box does. That means `local_first` does **not** simply prefer Ollama over
this second provider; with both marked `is_local=true`, it load-balances the two by measured
latency/queue depth. Use `quality_first` if the GPU/vLLM tier should be preferred whenever it's
healthy, or `balanced` for a blend of latency, cost, and quality tier.

No changes to `aura_core/llm/registry.py` or `router.py` were required for either option — this
was a deployment/configuration/test task, not a change to the routing engine itself.

## 5. Verification

- `pytest` and `pytest tests/architecture` remain green — nothing in `aura_core` changed.
- **Option A:** re-run the existing opt-in `tests/load/test_multi_model_routing.py`
  (`AURA_ROUTING_TEST_*` env vars) as the regression check.
- **Option B:** once a GPU host exists, bring up `--profile gpu`, confirm the `vllm` service's
  own healthcheck passes, then run
  `pytest tests/load -m load -s -k vllm_hybrid_routing` with the four `AURA_HYBRID_TEST_*` env
  vars set (see the test's module docstring) as the proof step before relying on it in
  production.
