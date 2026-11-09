#!/usr/bin/env bash
#
# Serve a model with vLLM's OpenAI-compatible API server.
#
# Simpler than the sibling llama.cpp entrypoint because vLLM does its own weight fetch: given
# `--model <hf-repo>` it downloads (and caches) from the Hugging Face Hub itself, so there is no
# separate curl-then-serve step here. Two properties this script still exists to guarantee,
# matching deploy/spaces/inference/entrypoint.sh:
#
#   1. It refuses to start without an API key. A Space is public by default, and an
#      unauthenticated OpenAI-compatible server on a paid GPU is free generation for anyone who
#      finds the URL -- billed to you. Failing to boot is a much better outcome.
#   2. It fails loudly rather than guessing: a missing/wrong model name is vLLM's own error, but
#      a missing key is caught here first, before any GPU time is spent.

set -euo pipefail

VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
PORT="${PORT:-7860}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.90}"
# The weights' Hub commit SHA. Required below: empty would serve whatever the repo's main branch
# is today, which is how an unannounced upstream push silently changes what answers.
MODEL_REVISION="${VLLM_MODEL_REVISION:-}"
# ADR-031 image understanding. 0 (the default) keeps this a text-only server whatever model it
# runs. A positive value is the per-prompt image cap, which must be >= the gateway's
# AURA_VISION_MAX_IMAGES, and needs a vision-language VLLM_MODEL (e.g. Qwen/Qwen3-VL-8B-Instruct).
MAX_IMAGES="${VLLM_MAX_IMAGES_PER_PROMPT:-0}"

# If the Space's disk is not persistent, this download repeats on every cold start after the
# Space sleeps -- the same argument the sibling Space makes against a free/sleeping tier, and
# the reason docs/guides/split-inference-deployment.md §8 recommends an always-on GPU flavor
# specifically for this node: a failover that is itself unreliably slow when needed defeats the
# point of having one.

if [ -z "${VLLM_API_KEY:-}" ]; then
    cat >&2 <<'ERR'
FATAL: VLLM_API_KEY is not set.

This Space would otherwise start as an unauthenticated, publicly reachable GPU inference
endpoint. Set it as a Space secret:

    Space -> Settings -> Variables and secrets -> New secret
    Name:  VLLM_API_KEY
    Value: <a long random string>

Then set the SAME value on the AURA gateway as AURA_PROVIDER_HFSPACE_API_KEY.
ERR
    exit 1
fi

# Required, not recommended (audit FSA-111): without a pinned revision an upstream push to the
# model repo silently changes what answers. The gateway sets AURA_PROVIDER_HFSPACE_MODEL_REVISION
# to the same value, so the two sides can be compared.
if [ -z "$MODEL_REVISION" ]; then
    cat >&2 <<'ERR'
FATAL: VLLM_MODEL_REVISION is not set.

Pin the weights to a commit of the model repo (Hub -> Files and versions -> copy the commit SHA):

    Space -> Settings -> Variables and secrets -> New variable
    Name:  VLLM_MODEL_REVISION
    Value: <the commit SHA>

Then set AURA_PROVIDER_HFSPACE_MODEL_REVISION to the same value on the AURA gateway.
ERR
    exit 1
fi

case "$MAX_IMAGES" in
    ''|*[!0-9]*) echo "FATAL: VLLM_MAX_IMAGES_PER_PROMPT must be a whole number, got '$MAX_IMAGES'" >&2; exit 1 ;;
esac

args=(
    --model "$VLLM_MODEL"
    --served-model-name "$VLLM_MODEL"
    --host 0.0.0.0
    --port "$PORT"
    --api-key "$VLLM_API_KEY"
    --max-model-len "$MAX_MODEL_LEN"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
)
if [ -n "$MODEL_REVISION" ]; then
    args+=(--revision "$MODEL_REVISION")
fi
if [ "$MAX_IMAGES" -gt 0 ]; then
    # The gateway sends images only as base64 data: URLs (ADR-031). vLLM would otherwise fetch
    # any http(s) image URL a caller names, from inside this container, which is a server-side
    # request forgery primitive for anyone holding the key. Allowing one domain that can never
    # resolve (RFC 2606 .invalid) refuses every remote fetch, and redirects stay off in case a
    # future default widens it. Local file:// media stays off (no --allowed-local-media-path),
    # and per-request processor kwargs stay refused (no --trust-request-mm-kwargs).
    export VLLM_MEDIA_URL_ALLOW_REDIRECTS=0
    args+=(
        --limit-mm-per-prompt "{\"image\": $MAX_IMAGES}"
        --allowed-media-domains media.invalid
    )
fi

echo "==> Serving $VLLM_MODEL${MODEL_REVISION:+@$MODEL_REVISION}  (max_model_len=$MAX_MODEL_LEN images_per_prompt=$MAX_IMAGES port=$PORT)"

exec python3 -m vllm.entrypoints.openai.api_server "${args[@]}"
