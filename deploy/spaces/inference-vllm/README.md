---
title: AURA Inference vLLM
emoji: ⚡
colorFrom: indigo
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: Real vLLM GPU failover endpoint for the AURA gateway
---

# AURA Inference (vLLM)

An OpenAI-compatible `/v1/chat/completions` endpoint backed by a real vLLM server, running on a
paid GPU-tier Hugging Face Space.

This is **not** a demo Space and there is no UI. It is a **failover** dependency for another
application: the AURA gateway's primary provider is Ollama on a Contabo VPS it controls, and
this node is only reached automatically when that primary is unhealthy (see
`docs/guides/split-inference-deployment.md` §8 in the AURA-AI repo). It will refuse to start
without an API key.

## Requires a paid GPU hardware tier

Unlike `deploy/spaces/inference/` (llama.cpp, CPU-only), vLLM needs CUDA. Choose a GPU flavor
(e.g. `t4-small`, `a10g-small`) when creating the Space — it will not run on `cpu-basic`, and
GPU tiers are billed hourly rather than a flat subscription fee.

## Required secret

| Name | Where | Value |
| --- | --- | --- |
| `VLLM_API_KEY` | set at creation with `hf repo create --secrets`, or Space → Settings → Variables and secrets | a long random string |

The same value goes on the gateway as `AURA_PROVIDER_HFSPACE_API_KEY`. Without it the container
exits at startup rather than serving an open endpoint — a Space is public by default, and an
unauthenticated vLLM server is free GPU generation for anyone who finds the URL, billed to you.

## Optional variables

| Name | Default | Notes |
| --- | --- | --- |
| `VLLM_MODEL` | `Qwen/Qwen2.5-7B-Instruct-AWQ` | any repo vLLM can serve; must match `AURA_PROVIDER_HFSPACE_MODEL` on the gateway |
| `VLLM_MAX_MODEL_LEN` | `8192` | keep in step with `AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW` on the gateway |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.90` | fraction of GPU memory vLLM is allowed to reserve |
| `VLLM_MODEL_REVISION` | **required** | Hub commit SHA that pins the weights; the Space refuses to start without it (audit FSA-111). Set `AURA_PROVIDER_HFSPACE_MODEL_REVISION` on the gateway to the same value |
| `VLLM_MAX_IMAGES_PER_PROMPT` | `0` | `0` = text only. A positive value enables image input for a vision-language `VLLM_MODEL` (e.g. `Qwen/Qwen3-VL-8B-Instruct`) and refuses every remote media URL. See `docs/deployment/inference-plane.md` §8 in the AURA-AI repo for the full vision configuration |

If the chosen model repo is gated on the Hub, also set an `HF_TOKEN` secret (authenticates the
weight *download*, not inference calls — a different thing from `VLLM_API_KEY`).

## Verifying it

```bash
curl -sS https://<user>-<space>.hf.space/v1/models \
  -H "Authorization: Bearer $VLLM_API_KEY" | jq

curl -sS https://<user>-<space>.hf.space/v1/chat/completions \
  -H "Authorization: Bearer $VLLM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-7B-Instruct-AWQ","messages":[{"role":"user","content":"reply with one word: ready"}]}' | jq -r '.choices[0].message.content'
```

A request **without** the header must return 401. If it returns a completion instead, the key is
not set and the endpoint is open — stop and fix that before pointing anything at it.

## What this costs, honestly

Running inference here means the system no longer satisfies two of its own rules: it stops
working without internet, and prompts plus retrieved document content leave infrastructure you
control. That trade is recorded in
[`docs/guides/split-inference-deployment.md`](https://github.com/anasibrahimgnedy-collab/AURA-AI/blob/main/docs/guides/split-inference-deployment.md)
§8, and the gateway will not talk to a host like this without an explicit
`AURA_ALLOW_REMOTE_INFERENCE=true` opt-in.

GPU Spaces bill by the hour, not a flat subscription. If this node is set to sleep when idle
(cheaper, but its disk is typically ephemeral too), every wake re-downloads the model before it
can answer its first failover request — an unreliable failover defeats the point of having one,
so an always-on GPU flavor is the recommended default for this specific node, at the cost of
paying for GPU time it mostly won't use.
