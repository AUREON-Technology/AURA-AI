# AURA inference endpoint, packaged as a Hugging Face Space.
#
# This serves an OpenAI-compatible /v1/chat/completions from a GGUF model running under
# llama.cpp. The AURA gateway on the VPS talks to it as an `openai_compatible` provider, which
# is a configuration change on that side and no code change at all (ADR-003).
#
# WHAT THIS IS NOT: it is not the Hugging Face Inference API. Nothing here calls a hosted model.
# This is our container, our weights, our server process -- on infrastructure we do not own. That
# distinction is real, and it is also not a loophole: see docs/guides/split-inference-deployment.md
# for what this costs against the self-hosting rules, and why the gateway now demands an explicit
# opt-in before it will talk to a host like this.
#
# Base image is the upstream llama.cpp server build. Pinned by tag rather than digest because HF
# rebuilds Spaces on push and a stale digest is a silent build failure at the worst moment; if you
# want reproducibility, pin the digest here and accept that you must bump it deliberately.
FROM ghcr.io/ggml-org/llama.cpp:server

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Spaces run the container as uid 1000. Creating the user explicitly (rather than relying on the
# base image having one) keeps the model cache in a directory we know is writable.
RUN useradd -m -u 1000 -s /bin/bash aura || true

COPY --chown=aura:aura entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER aura
ENV HOME=/home/aura \
    MODEL_DIR=/home/aura/models

# Spaces expects 7860 unless `app_port` says otherwise. README.md sets it to match.
EXPOSE 7860

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
