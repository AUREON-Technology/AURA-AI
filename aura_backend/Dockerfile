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
COPY requirements.lock .

RUN pip install --no-cache-dir --user -r requirements.lock

# ==========================================
# STAGE 2: Runner
# ==========================================
FROM python:3.11-slim as runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/root/.local/bin:$PATH

WORKDIR /app

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
