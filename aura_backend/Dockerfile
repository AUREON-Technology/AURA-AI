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
COPY --from=builder /root/.local /root/.local

# Copy application source code
COPY . .

# Create volume/directory for local qdrant storage if running locally
RUN mkdir -p local_qdrant_storage \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

# Hydrate *_FILE secrets and mount /api/v3. Compose may override the command
# (extra uvicorn flags) but must not skip this entrypoint: it wipes the
# Prometheus multiprocess directory before workers spawn.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Workers via AURA_WORKERS, same default as deploy/docker-compose.prod.yml.
CMD ["sh", "-c", "uvicorn aura_service:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${AURA_WORKERS:-4} --proxy-headers --log-level info"]
