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

echo "==> Serving $VLLM_MODEL  (max_model_len=$MAX_MODEL_LEN port=$PORT)"

exec python3 -m vllm.entrypoints.openai.api_server \
    --model "$VLLM_MODEL" \
    --served-model-name "$VLLM_MODEL" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --api-key "$VLLM_API_KEY" \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
