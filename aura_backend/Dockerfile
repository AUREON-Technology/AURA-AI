# ==========================================
# STAGE 1: Builder
# ==========================================
FROM python:3.11-slim as builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Pin the runtime set. requirements.lock is compiled from requirements.txt with the
# command recorded in its own header. Test/dev extras stay in requirements-dev.txt and
# never land here.
#
# The lock pins torch to PyTorch's CPU wheel (torch==2.13.0+cpu). That is deliberate and
# worth keeping: the default Linux torch wheel drags in the nvidia-*, cuda-* and triton
# packages, which is 6.5 GB of CUDA runtime that a CPU-only host can never execute. Four
# services build from this context, so the cost is paid four times over.
#
# Hash-checked (audit F-38 / SEC-10). Every file pip downloads must match a sha256 recorded
# in the lock, so neither index can substitute a package -- the dependency-confusion pattern
# of the 2022 torchtriton incident. The lock is compiled with `--torch-backend cpu`, which
# resolves only the PyTorch packages against the CPU index and everything else against PyPI,
# and it names no index itself (a requirements-file --index-url would discard the flag
# below), so the CPU index is named here. Without it the install fails outright -- it cannot
# silently fall back to the CUDA build, because those hashes are not in the lock.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
COPY requirements.lock requirements-train.lock ./

RUN pip install --no-cache-dir --user --require-hashes \
        --extra-index-url "$TORCH_INDEX_URL" -r requirements.lock

# Optional LoRA training dependencies (peft; ADR-008 Loop B, GAP-017). Off by default so the
# gateway and every other service never carry them; only `worker-training` builds with
# AURA_TRAINING_DEPS=true. requirements-train.lock is compiled from requirements-train.txt,
# which constrains itself to requirements.lock, so this cannot move torch/transformers away
# from the gateway's pins; it is hash-checked the same way.
ARG AURA_TRAINING_DEPS=false
RUN if [ "$AURA_TRAINING_DEPS" = "true" ]; then \
        pip install --no-cache-dir --user --require-hashes \
            --extra-index-url "$TORCH_INDEX_URL" -r requirements-train.lock; \
    fi

# ==========================================
# STAGE 2: Runner
# ==========================================
FROM python:3.11-slim as runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# The builder installs with `pip --user` into /root/.local. The runner copies that tree to
# /opt/aura-python and points PYTHONUSERBASE at it, so the same packages are found whichever uid
# runs the process: the image's own `aura` user (Railway), or root where the compose files still
# set `user: "0:0"` (the VPS stacks, until their secret files and volumes are migrated). /root is
# mode 0700, so a non-root process could never have read /root/.local.
ENV PYTHONUSERBASE=/opt/aura-python
ENV PATH=/opt/aura-python/bin:$PATH

WORKDIR /app

# Phase F1/F2: the system binaries pytesseract and the espeak-ng TTS adapter shell out to.
# Runtime-only, so these belong in the runner stage, not the builder.
#
# Piper 2023.11.14-2 linux x86_64 is the last rhasspy binary release (repo archived).
# SHA256 of piper_linux_x86_64.tar.gz was computed from the GitHub release asset.
# Voices are pinned to huggingface.co/rhasspy/piper-voices v1.0.0 (not committed to git):
#   onnx SHA256 from the Hugging Face LFS pointer; json SHA256 from the resolve URL.
ARG PIPER_URL=https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz
ARG PIPER_SHA256=a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992
ARG PIPER_EN_ONNX_URL=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
ARG PIPER_EN_ONNX_SHA256=5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f
ARG PIPER_EN_JSON_URL=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
ARG PIPER_EN_JSON_SHA256=efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0
ARG PIPER_AR_ONNX_URL=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx
ARG PIPER_AR_ONNX_SHA256=9e95cab07b679da603bba17c4dec7ab3111320571964ee95c0379603c086491e
ARG PIPER_AR_JSON_URL=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json
ARG PIPER_AR_JSON_SHA256=ea6d9b9d9076dbdb6bf5c98c6a141ef154959d2359709b37855727964e7d6c4d

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-ara \
    espeak-ng \
    ca-certificates \
    curl \
    && curl -fsSL --retry 3 --retry-delay 2 "${PIPER_URL}" -o /tmp/piper.tar.gz \
    && echo "${PIPER_SHA256}  /tmp/piper.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/piper /opt/piper/voices \
    && tar -xzf /tmp/piper.tar.gz -C /opt/piper --strip-components=1 \
    && chmod +x /opt/piper/piper \
    && curl -fsSL --retry 3 --retry-delay 2 "${PIPER_EN_ONNX_URL}" \
        -o /opt/piper/voices/en_US-lessac-medium.onnx \
    && echo "${PIPER_EN_ONNX_SHA256}  /opt/piper/voices/en_US-lessac-medium.onnx" | sha256sum -c - \
    && curl -fsSL --retry 3 --retry-delay 2 "${PIPER_EN_JSON_URL}" \
        -o /opt/piper/voices/en_US-lessac-medium.onnx.json \
    && echo "${PIPER_EN_JSON_SHA256}  /opt/piper/voices/en_US-lessac-medium.onnx.json" | sha256sum -c - \
    && curl -fsSL --retry 3 --retry-delay 2 "${PIPER_AR_ONNX_URL}" \
        -o /opt/piper/voices/ar_JO-kareem-medium.onnx \
    && echo "${PIPER_AR_ONNX_SHA256}  /opt/piper/voices/ar_JO-kareem-medium.onnx" | sha256sum -c - \
    && curl -fsSL --retry 3 --retry-delay 2 "${PIPER_AR_JSON_URL}" \
        -o /opt/piper/voices/ar_JO-kareem-medium.onnx.json \
    && echo "${PIPER_AR_JSON_SHA256}  /opt/piper/voices/ar_JO-kareem-medium.onnx.json" | sha256sum -c - \
    && rm -f /tmp/piper.tar.gz \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/piper:${PATH}" \
    LD_LIBRARY_PATH="/opt/piper"

# Copy installed python packages from builder stage
COPY --from=builder /root/.local /opt/aura-python

# Copy application source code
COPY . .

# Run as an unprivileged user (uid/gid 10002; the sandbox image uses 10001, and the two never
# share a mount). The application code stays owned by root, so the process cannot rewrite it.
# What it may write:
#   - local_qdrant_storage: the legacy services/rag.py client opens it relative to /app on import.
#   - /var/lib/aura: DATA_DIR (learning logs, drive ingest) and PROMETHEUS_MULTIPROC_DIR live
#     under it. The compose files mount their volumes there and set DATA_DIR themselves.
#   - $HOME (/home/aura): the Hugging Face cache faster-whisper downloads its model into.
# A platform volume mounted as root (Railway mounts volumes root-owned) is not writable by this
# user: such a deploy needs the volume's ownership fixed or RAILWAY_RUN_UID=0.
RUN groupadd --system --gid 10002 aura \
    && useradd --system --uid 10002 --gid aura --home-dir /home/aura --create-home \
        --shell /usr/sbin/nologin aura \
    && mkdir -p local_qdrant_storage /var/lib/aura/data \
    && chown aura:aura local_qdrant_storage /var/lib/aura /var/lib/aura/data \
    && chmod +x /app/docker-entrypoint.sh

# data_dir's default is "." (/app), which this user cannot write. DATA_DIR (the compose files
# set it) still takes precedence over this: config.Settings reads DATA_DIR before AURA_DATA_DIR.
ENV AURA_DATA_DIR=/var/lib/aura/data

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

USER aura

# Hydrate *_FILE secrets and mount /api/v3. Compose may override the command
# (extra uvicorn flags) but must not skip this entrypoint: it wipes the
# Prometheus multiprocess directory before workers spawn.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Workers via AURA_WORKERS, same default as deploy/docker-compose.prod.yml.
# --forwarded-allow-ips: uvicorn honours X-Forwarded-For/-Proto only from these peers; without
# it `--proxy-headers` trusts only 127.0.0.1, so on Railway (edge proxy on another host) every
# request carried the proxy's address and plain-http scheme (audit FSA-005). `*` is right
# wherever the only path to this port is a proxy we control -- Railway's edge, nginx on the
# VPS (the compose files already pass the same flag) -- and must be narrowed to the proxy's
# address if the port is ever exposed directly. With `*`, uvicorn's request.client is the
# *leftmost* X-Forwarded-For entry, which the caller writes; nothing may treat it as the client's
# address. The v3 router resolves it from the right instead (`client_ip` in aura_core/api/v3.py,
# for both the rate limiter and audit records; pinned by tests/unit/test_audit_source_ip.py).
# The default is quoted on purpose: unquoted, `sh -c` globbed `*` to every file in WORKDIR /app,
# uvicorn got dozens of stray arguments and exited before serving -- every Railway build from
# c575904 (2026-11-06) on, unless AURA_FORWARDED_ALLOW_IPS was set
# (tests/unit/test_backend_image_user.py).
# This is the command Railway runs. `exec` makes uvicorn PID 1, so the platform's SIGTERM reaches
# it rather than a shell, and --timeout-graceful-shutdown bounds the drain below railway.json's
# drainingSeconds (audit F-25; the compose files carry the same pair on the gateway).
CMD ["sh", "-c", "exec uvicorn aura_service:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${AURA_WORKERS:-4} --proxy-headers --forwarded-allow-ips \"${AURA_FORWARDED_ALLOW_IPS:-*}\" --log-level info --timeout-graceful-shutdown ${AURA_GRACEFUL_SHUTDOWN_S:-150}"]
