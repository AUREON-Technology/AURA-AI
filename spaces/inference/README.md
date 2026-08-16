---
title: AURA Inference
emoji: 🧠
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: OpenAI-compatible llama.cpp endpoint for the AURA gateway
---

# AURA Inference

An OpenAI-compatible `/v1/chat/completions` endpoint backed by llama.cpp, serving a GGUF model to
the AURA gateway running on a separate host.

This is **not** a demo Space and there is no UI. It is a backend dependency for another
application, and it will refuse to start without an API key.

## Required secret

| Name | Where | Value |
| --- | --- | --- |
| `LLAMA_API_KEY` | Space → Settings → Variables and secrets | a long random string |

The same value goes on the gateway as `AURA_PROVIDER_REMOTE_API_KEY`. Without it the container
exits at startup rather than serving an open endpoint — a Space is public by default, and an
unauthenticated llama-server is free generation for anyone who finds the URL.

## Optional variables

| Name | Default | Notes |
| --- | --- | --- |
| `MODEL_REPO` | `bartowski/Qwen2.5-3B-Instruct-GGUF` | any repo hosting a single-file GGUF |
| `MODEL_FILE` | `Qwen2.5-3B-Instruct-Q4_K_M.gguf` | **verify this exact filename in the repo** — names vary, and a wrong one is a 404 |
| `CTX_SIZE` | `8192` | RAG turns arrive with a lot of context |
| `THREADS` | `nproc` | |

3B is the default because free Spaces are 2 vCPU and time-to-first-token is dominated by prompt
processing, not generation rate. A 7B fits the 16 GB of RAM comfortably and is the better model —
switch if you would rather have quality than latency, and measure rather than guessing which you
want.

## Verifying it

```bash
curl -sS https://<user>-<space>.hf.space/v1/models \
  -H "Authorization: Bearer $LLAMA_API_KEY" | jq

curl -sS https://<user>-<space>.hf.space/v1/chat/completions \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"reply with one word: ready"}]}' | jq -r '.choices[0].message.content'
```

A request **without** the header must return 401. If it returns a completion instead, the key is
not set and the endpoint is open — stop and fix that before pointing anything at it.

## What this costs, honestly

Running inference here means the system no longer satisfies two of its own rules: it stops working
without internet, and prompts plus retrieved document content leave infrastructure you control.
That is a deliberate trade recorded in
[`docs/guides/split-inference-deployment.md`](https://github.com/anasibrahimgnedy-collab/AURA-AI/blob/main/docs/guides/split-inference-deployment.md),
and the gateway will not talk to a host like this without an explicit opt-in flag.

On the free CPU tier the Space also sleeps when idle, and its disk is ephemeral — so every wake
re-downloads the model before it can answer. That is the main reason this is a good way to prove
the wiring and a poor way to serve users.
