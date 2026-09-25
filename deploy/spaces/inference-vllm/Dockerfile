# AURA GPU failover endpoint, packaged as a Hugging Face Space.
#
# This serves an OpenAI-compatible /v1/chat/completions from a real vLLM server (the
# vllm-openai image, not llama.cpp). Unlike deploy/spaces/inference/ (which replaces Ollama
# outright on a CPU Space), this node is meant to be a FAILOVER: the AURA gateway on the
# Contabo VPS talks to it as a second `openai_compatible` provider, declared `is_local=false`
# so `local_first` routing only reaches it when the Contabo Ollama provider is unhealthy
# (ADR-003's per-provider circuit breaker) -- see docs/guides/split-inference-deployment.md §8.
#
# WHAT THIS IS NOT: it is not the Hugging Face Inference API. This is our container, our
# weights, our server process, running on rented GPU compute we do not own -- same "our
# infrastructure, not a vendor" framing as deploy/spaces/inference/README.md, with the same
# honest costs: the gateway demands AURA_ALLOW_REMOTE_INFERENCE=true before it will talk to a
# host like this at all.
#
# Requires a PAID GPU hardware tier when the Space is created (e.g. t4-small, a10g-small) --
# vLLM needs CUDA, so this will not run on cpu-basic. Pinned by tag, not digest, for the same
# reason as the sibling Space: HF rebuilds on push, and a stale digest is a silent build
# failure at the worst moment. Pin the digest yourself if you want reproducibility and are
# willing to bump it deliberately.
FROM vllm/vllm-openai:latest

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Spaces run the container as uid 1000. If this breaks GPU device visibility on your chosen
# hardware tier, drop back to USER root below and note why -- CUDA base images vary on this,
# and it can only be verified against a real GPU build, which this repo cannot do for you.
#
# `useradd -u 1000 ... || true` used to swallow a UID collision silently: vllm-openai's base
# image already has a uid-1000 user (Ray provisions one), so useradd failed, `|| true` hid it,
# and no `aura` user ever existed -- USER aura then failed at container start with "references
# a user that is not present in /etc/passwd" (caught live on a real GPU build). Renaming
# whichever user already holds uid 1000 to `aura` -- or creating one fresh if none does --
# guarantees `aura` exists at uid 1000 either way.
RUN existing_user="$(getent passwd 1000 | cut -d: -f1)"; \
    if [ -n "$existing_user" ]; then \
        usermod -l aura -d /home/aura -m "$existing_user"; \
    else \
        useradd -m -u 1000 -s /bin/bash aura; \
    fi

COPY --chown=aura:aura entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER aura
ENV HOME=/home/aura

# Spaces expects 7860 unless `app_port` says otherwise. README.md sets it to match.
EXPOSE 7860

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
