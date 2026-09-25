#!/usr/bin/env bash
#
# Seed Loop B console data on the aura-dev stack only.
#
# Wipes dev Loop B tables, inserts curated feedback, and runs the full pipeline
# (FINETUNE approval â†’ LocalLora job on worker-training â†’ canary model â†’ pending
# MODEL_PROMOTION approval). Refuses the production ``aura`` compose project.
#
# Usage (from repo root on the VPS):
#   ./deploy/scripts/seed-dev.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT="${COMPOSE_PROJECT_NAME:-aura-dev}"
ENV_FILE="${AURA_DEV_ENV_FILE:-$DEPLOY_DIR/.env.development}"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.dev.yml"

if [ "$PROJECT" = "aura" ]; then
  echo "FAILED: refusing to seed production compose project (aura)" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "FAILED: missing env file $ENV_FILE (copy from .env.development.example)" >&2
  exit 1
fi

COMPOSE=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

say() { printf '\n==> %s\n' "$*"; }

say "Building dev gateway and worker-training (seed + Celery fixes)"
"${COMPOSE[@]}" build dev-gateway worker-training

say "Recreating worker-training with dev env (CPU LoRA flag; image built with requirements-train.txt)"
"${COMPOSE[@]}" --profile training up -d --force-recreate worker-training
sleep 10

say "Running seed_dev_console.py in gateway"
"${COMPOSE[@]}" run --rm \
  --no-deps \
  -e COMPOSE_PROJECT_NAME="$PROJECT" \
  -e PYTHONPATH=/app \
  dev-gateway python scripts/seed_dev_console.py

say "Done â€” open https://$(grep -E '^AURA_DOMAIN=' "$ENV_FILE" | head -1 | cut -d= -f2-)/console/learning"
