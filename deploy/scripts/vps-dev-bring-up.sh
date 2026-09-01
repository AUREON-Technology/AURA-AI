#!/usr/bin/env bash
#
# TLS dual-SAN renewal + aura-dev stack bring-up + optional seed.
# Invoked from ci-deploy.sh when AURA_DEV_OPS=1 (GitHub Actions Deploy VPS).
#
# Env (all default 1 when unset):
#   AURA_DEV_OPS_TLS   renew-tls-dual-san.sh
#   AURA_DEV_OPS_UP    compose up, alembic
#   AURA_DEV_OPS_SEED  seed-dev.sh (wipes dev Loop B tables)
#
set -euo pipefail

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

REPO="${AURA_REPO:-/opt/aura-stack}"
DEPLOY="${AURA_DEPLOY_DIR:-$REPO/deploy}"
TLS="${AURA_DEV_OPS_TLS:-1}"
UP="${AURA_DEV_OPS_UP:-1}"
SEED="${AURA_DEV_OPS_SEED:-1}"

[ -d "$DEPLOY" ] || die "deploy dir not found: $DEPLOY"
cd "$DEPLOY"

ENV_FILE="$DEPLOY/.env"
DEV_ENV="$DEPLOY/.env.development"

ensure_dev_domain() {
  if [ ! -f "$ENV_FILE" ]; then
    die "missing $ENV_FILE"
  fi
  if ! grep -qE '^AURA_DEV_DOMAIN=.+$' "$ENV_FILE"; then
    say "Adding AURA_DEV_DOMAIN to deploy/.env"
    printf '\nAURA_DEV_DOMAIN=dev.169.58.188.43.nip.io\n' >> "$ENV_FILE"
  fi
}

if [ ! -f "$DEV_ENV" ] && [ -f "$DEPLOY/.env.development.example" ]; then
  cp "$DEPLOY/.env.development.example" "$DEV_ENV"
  say "Created .env.development from example"
fi

ensure_dev_domain

if [ "$TLS" = "1" ]; then
  say "Dual-SAN TLS renewal"
  chmod +x scripts/renew-tls-dual-san.sh
  ./scripts/renew-tls-dual-san.sh
fi

if [ "$UP" = "1" ]; then
  say "Starting aura-dev data layer"
  docker compose -p aura-dev -f docker-compose.dev.yml \
    --env-file .env.development up -d postgres redis qdrant

  say "Waiting for postgres"
  for attempt in $(seq 1 30); do
    if docker compose -p aura-dev -f docker-compose.dev.yml \
      --env-file .env.development exec -T postgres pg_isready -U aura >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  say "Alembic migrate (dev)"
  docker compose -p aura-dev -f docker-compose.dev.yml \
    --env-file .env.development run --rm gateway alembic upgrade head

  say "Starting aura-dev app layer"
  docker compose -p aura-dev -f docker-compose.dev.yml \
    --env-file .env.development --profile training up -d
fi

if [ "$SEED" = "1" ]; then
  say "Seeding dev console (Loop B)"
  chmod +x scripts/seed-dev.sh
  ./scripts/seed-dev.sh
fi

say "Dev health check"
curl -fsSk "https://dev.169.58.188.43.nip.io/health/ready" | head -c 500 || die "dev /health/ready failed"
echo ""
