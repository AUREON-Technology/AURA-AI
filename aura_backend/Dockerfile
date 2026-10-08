# AURA GPU failover endpoint, packaged as a Hugging Face Space.
#
# The lock resolves torch from PyTorch's CPU index, and carries both index URLs itself so
# this install needs no extra flags. That is deliberate and worth keeping: the default
# Linux torch wheel drags in the nvidia-*, cuda-* and triton packages, which is 6.5 GB of
# CUDA runtime that a CPU-only host can never execute. Four services build from this
# context, so the cost is paid four times over. Re-compiling without the CPU index will
# silently put all of it back.
COPY requirements.lock requirements-train.txt ./

RUN pip install --no-cache-dir --user -r requirements.lock

# Optional LoRA training dependencies (peft; ADR-008 Loop B, GAP-017). Off by default so the
# gateway and every other service never carry them; only `worker-training` builds with
# AURA_TRAINING_DEPS=true. requirements-train.txt constrains itself to requirements.lock, so
# this cannot move torch/transformers away from the gateway's pins.
ARG AURA_TRAINING_DEPS=false
RUN if [ "$AURA_TRAINING_DEPS" = "true" ]; then \
        pip install --no-cache-dir --user -r requirements-train.txt; \
    fi

# ==========================================
# STAGE 2: Runner
# ==========================================
FROM python:3.11-slim as runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/root/.local/bin:$PATH

WORKDIR /app

# Phase F1/F2: the system binaries pytesseract and the espeak-ng TTS adapter shell out to.
# Runtime-only, so these belong in the runner stage, not the builder.
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

# Release identity (BLOCK-03). Deliberately the last layers before EXPOSE: these change on
# every commit, so declaring them earlier would invalidate the pip install and the piper
# download on each build. Baked into the image rather than passed as compose `environment:`
# so all five services built from this Dockerfile report the same revision without five
# copies of the same env block -- and so the identity cannot be edited on the running host,
# which is the whole point of calling it immutable.
#
# Empty when nothing is passed; AuraSettings.validate() is what refuses an unidentified
# build in production, so a local `docker build` with no args still works.
ARG GIT_SHA=""
ARG GIT_REF=""
ARG BUILD_TIME=""
ARG DEPLOYMENT_ID=""
ENV AURA_GIT_SHA=${GIT_SHA} \
    AURA_GIT_REF=${GIT_REF} \
    AURA_BUILD_TIME=${BUILD_TIME} \
    AURA_DEPLOYMENT_ID=${DEPLOYMENT_ID}

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
