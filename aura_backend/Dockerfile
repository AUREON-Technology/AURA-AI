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

# Pin the runtime set. requirements.lock is compiled from requirements.txt
# (`uv pip compile --python-version 3.11 --python-platform linux`). Test/dev
# extras stay in requirements-dev.txt and never land here.
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
