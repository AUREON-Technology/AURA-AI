#!/usr/bin/env bash
#
# Fetch the GGUF if it is not already cached, then serve it.
#
# Two properties this script exists to guarantee:
#
#   1. It refuses to start without an API key. A Space is public by default, and llama-server
#      without --api-key is an open inference endpoint: anyone who finds the URL gets free
#      generation, and on a metered tier, free generation billed to you. Failing to boot is a
#      much better outcome than serving strangers quietly.
#   2. It fails loudly on a bad download. curl without -f treats a 404 HTML page as success and
#      writes it to disk, at which point llama-server fails with a confusing parse error about a
#      file that looks superficially fine. Better to fail at the download.

set -euo pipefail

MODEL_REPO="${MODEL_REPO:-bartowski/Qwen2.5-3B-Instruct-GGUF}"
MODEL_FILE="${MODEL_FILE:-Qwen2.5-3B-Instruct-Q4_K_M.gguf}"
MODEL_DIR="${MODEL_DIR:-/home/aura/models}"
CTX_SIZE="${CTX_SIZE:-8192}"
THREADS="${THREADS:-$(nproc)}"
PORT="${PORT:-7860}"

# 3B rather than 7B by default: free Spaces are 2 vCPU, and time-to-first-token is dominated by
# prompt processing, which a RAG turn makes expensive. A 7B fits the 16 GB of RAM comfortably and
# is still the better model -- set MODEL_REPO/MODEL_FILE if you would rather have quality than
# latency. Measure before assuming which one you want.

if [ -z "${LLAMA_API_KEY:-}" ]; then
    cat >&2 <<'ERR'
FATAL: LLAMA_API_KEY is not set.

This Space would otherwise start as an unauthenticated, publicly reachable inference endpoint.
Set it as a Space secret:

    Space -> Settings -> Variables and secrets -> New secret
    Name:  LLAMA_API_KEY
    Value: <a long random string>

Then set the SAME value on the AURA gateway as AURA_PROVIDER_REMOTE_API_KEY.
ERR
    exit 1
fi

mkdir -p "$MODEL_DIR"
MODEL_PATH="$MODEL_DIR/$MODEL_FILE"

if [ ! -s "$MODEL_PATH" ]; then
    echo "==> Downloading ${MODEL_REPO}/${MODEL_FILE}"
    # Free Spaces have ephemeral disk, so this runs again after every cold start. That is the
    # single biggest argument against the free tier for a backend dependency: a wake costs a
    # multi-gigabyte download before the first token, every time.
    URL="https://huggingface.co/${MODEL_REPO}/resolve/main/${MODEL_FILE}"
    if ! curl -fL --retry 3 --retry-delay 5 -o "${MODEL_PATH}.part" "$URL"; then
        echo "FATAL: download failed from $URL" >&2
        echo "       Check MODEL_REPO and MODEL_FILE -- GGUF filenames vary between repos," >&2
        echo "       and a wrong name gives a 404 rather than a helpful message." >&2
        rm -f "${MODEL_PATH}.part"
        exit 1
    fi
    # Rename only after a complete download, so an interrupted pull is retried rather than
    # served as a truncated model.
    mv "${MODEL_PATH}.part" "$MODEL_PATH"
fi

echo "==> Serving $MODEL_FILE  (ctx=$CTX_SIZE threads=$THREADS port=$PORT)"
ls -lh "$MODEL_PATH"

exec llama-server \
    --model "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --ctx-size "$CTX_SIZE" \
    --threads "$THREADS" \
    --api-key "$LLAMA_API_KEY" \
    --metrics \
    --timeout 600
