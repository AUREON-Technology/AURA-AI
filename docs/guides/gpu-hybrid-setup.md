# GPU Hybrid Setup — Ollama (CPU) + vLLM (GPU)

Adding a GPU-backed vLLM node as a second LLM provider alongside the existing CPU Ollama node,
so quality-sensitive turns can use a larger model without moving the whole stack off the
CPU-only VPS. See `docs/reports/OLLAMA_VLLM_COMPARISON.md` for the analysis behind this — in
short: Ollama stays the always-on, zero-cost, offline-capable default; vLLM only earns its keep
on a GPU, so it's an addition, not a replacement.

This is a config/deployment change, not a code change — `aura_core/llm/registry.py` and
`router.py` already support any number of providers. Everything below already exists in the
repo, gated so it is inert until you actually have a GPU host.

This guide covers a co-located GPU node, preferred via `quality_first` whenever it's healthy. For
a topology where Ollama stays primary and a Hugging Face Space GPU node is used only as failover
when Ollama is down, see
[`split-inference-deployment.md` §8](split-inference-deployment.md#8-gpu-vllm-variant-contabo-primary-space-as-failover)
instead — same underlying router, opposite preference.

---

## 0. Before you start

You need a machine (owned or rented) with an NVIDIA GPU, drivers installed, and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
so Docker can see the GPU. Enough VRAM to hold the model you intend to run — per
`docs/reports/AI_HARDWARE_CAPACITY_REPORT.md`, 24 GB VRAM is a reasonable target for a
mid-size AWQ/GPTQ-quantized model.

If that GPU host is rented compute you don't otherwise control (a Hugging Face Space, Cloud
Run, Fly.io, etc.), `AURA_ALLOW_REMOTE_INFERENCE=true` is required — see
`aura_core/config/external_inference.py`'s `REMOTE_INFERENCE_HOSTS`. A GPU box on your own
network needs no such flag; it's neither a banned hosted-vendor host nor a gated rented-compute
host.

## 1. Bring up the vLLM service

The `vllm` service in `deploy/docker-compose.prod.yml` is gated behind the `gpu` compose
profile, so it never starts on a plain `docker compose up -d` — only when asked for explicitly:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env --profile gpu up -d vllm
```

It serves whatever `AURA_VLLM_MODEL` names (default `Qwen/Qwen2.5-32B-Instruct-AWQ`) over
`/v1/chat/completions`, downloads weights from Hugging Face into the `vllm_models` volume on
first start, and reports healthy once `GET /v1/models` responds. Watch it come up:

```bash
docker compose -f deploy/docker-compose.prod.yml ps vllm
docker compose -f deploy/docker-compose.prod.yml logs -f vllm
```

If the GPU host is a separate machine rather than co-located in this compose stack, run vLLM
there directly instead (`docker run --gpus all vllm/vllm-openai:latest --model ...`) and skip
this step — either way, what matters is that something OpenAI-compatible ends up reachable at a
`/v1` URL from the gateway.

## 2. Add the second provider

In the real `deploy/.env` (not the example file), add:

```bash
AURA_LLM_PROVIDERS=local,large
AURA_PROVIDER_LARGE_KIND=openai_compatible
AURA_PROVIDER_LARGE_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ
AURA_PROVIDER_LARGE_BASE_URL=http://vllm:8000/v1
AURA_PROVIDER_LARGE_QUALITY_TIER=3
AURA_PROVIDER_LARGE_TIMEOUT_S=180
AURA_PROVIDER_LARGE_MAX_CONCURRENCY=4
AURA_PROVIDER_LARGE_IS_LOCAL=true
```

Use `http://vllm:8000/v1` if vLLM is the compose service from step 1; otherwise point
`AURA_PROVIDER_LARGE_BASE_URL` at wherever it's actually reachable
(`http://gpu-host.internal:8000/v1`, etc.).

`AURA_PROVIDER_LARGE_MODEL` must match `AURA_VLLM_MODEL` from step 1 — the gateway asks vLLM for
completions by that exact model name.

Then restart the services that build the LLM container so they pick up the new provider:

```bash
docker compose -f deploy/docker-compose.prod.yml up -d gateway worker-realtime worker-ingestion beat
```

## 3. Pick a routing policy

`AURA_PROVIDER_LARGE_IS_LOCAL=true` is deliberate, not a copy-paste default: `is_local` means
"your own infrastructure, not a vendor" (ADR-003), and a GPU box you own satisfies that just as
much as the CPU Ollama box. That has one practical consequence worth knowing before you pick
`AURA_LLM_ROUTING_POLICY`:

- **`local_first`** (the current default in `deploy/.env.example`) does **not** simply prefer
  `local` (Ollama) over `large` (vLLM) here, because both are `is_local=true`. It load-balances
  the two by measured latency and queue depth instead.
- **`quality_first`** always prefers the higher `quality_tier` provider when it's healthy — with
  `local` at tier 1 and `large` at tier 3, this reaches vLLM for essentially every turn. Use this
  if the GPU node should be the default and Ollama is really just the failover.
- **`balanced`** (the code-level default) blends latency, cost, and quality tier — a reasonable
  middle ground if you want most turns to land wherever is fastest right now, with quality tier
  breaking close ties.

There is no per-request "route this one to the GPU" toggle from configuration alone — that would
need call-site changes deciding which turns count as quality-sensitive, which is out of scope
here. Pick the policy that matches how you actually want the two providers to share load.

## 4. Validate before trusting it in production

The project's own roadmap flags that vLLM has never actually been run against this codebase —
it's supported "by protocol" (OpenAI-compatible), not by having been tried. Close that gap before
routing real traffic to it, the same way Phase G2 proved multi-Ollama routing for real:

```bash
export AURA_HYBRID_TEST_OLLAMA_BASE_URL=http://localhost:11434
export AURA_HYBRID_TEST_OLLAMA_MODEL=qwen2.5:3b-instruct-q4_K_M
export AURA_HYBRID_TEST_VLLM_BASE_URL=http://localhost:8000/v1
export AURA_HYBRID_TEST_VLLM_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ
cd aura_backend
pytest tests/load -m load -s -k vllm_hybrid_routing
```

This runs real health checks and real completions against both providers, and confirms
`QUALITY_FIRST` actually reaches the vLLM-backed model — see
`tests/load/test_vllm_hybrid_routing.py` for exactly what it proves.

## 5. Rolling back

If the GPU host goes away or misbehaves, remove `large` from `AURA_LLM_PROVIDERS` (back to
`AURA_LLM_PROVIDERS=local`) and restart the gateway/workers — the router only ever builds
providers named in that list, so this is a clean, immediate revert with no other config to undo.
The circuit breaker also means a temporarily-unreachable `large` provider degrades to Ollama
automatically before that: `ProviderRouter` opens its circuit after repeated failures and routes
around it, it doesn't need the config change to keep serving traffic.
