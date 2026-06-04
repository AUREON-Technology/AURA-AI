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

COPY requirements.txt .

# Install dependencies into the local user folder to copy easily
RUN pip install --no-cache-dir --user -r requirements.txt

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
RUN mkdir -p local_qdrant_storage

EXPOSE 8000

# Run optimized uvicorn server in production, binding to the environment PORT if set
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4 --log-level info"]
