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
info() { printf '    %s\n' "$*"; }
die() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

REPO="${AURA_REPO:-/opt/aura-stack}"
DEPLOY="${AURA_DEPLOY_DIR:-$REPO/deploy}"
TLS="${AURA_DEV_OPS_TLS:-1}"
UP="${AURA_DEV_OPS_UP:-1}"
SEED="${AURA_DEV_OPS_SEED:-0}"

[ -d "$DEPLOY" ] || die "deploy dir not found: $DEPLOY"
cd "$DEPLOY"

ENV_FILE="$DEPLOY/.env"
DEV_ENV="$DEPLOY/.env.development"


ensure_superadmin_from_example() {
  local example="$DEPLOY/.env.development.example"
  [ -f "$DEV_ENV" ] || return 0
  [ -f "$example" ] || return 0
  local example_line
  example_line="$(grep -E '^SUPERADMIN_EMAILS=' "$example" | head -1 || true)"
  [ -n "$example_line" ] || return 0
  if grep -qF "$example_line" "$DEV_ENV" 2>/dev/null; then
    return 0
  fi
  if grep -qE '^SUPERADMIN_EMAILS=' "$DEV_ENV" 2>/dev/null; then
    sed -i "s/^SUPERADMIN_EMAILS=.*/$example_line/" "$DEV_ENV"
  else
    printf '\n%s\n' "$example_line" >> "$DEV_ENV"
  fi
  say "Synced SUPERADMIN_EMAILS in .env.development from example"
}

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
ensure_superadmin_from_example

if [ "$TLS" = "1" ]; then
  say "Dual-SAN TLS renewal"
  chmod +x scripts/renew-tls-dual-san.sh
  ./scripts/renew-tls-dual-san.sh
fi

if [ "$UP" = "1" ]; then
  say "Stopping stale dev gateway (if any) â€” console left running to avoid nginx 502"
  docker compose -p aura-dev -f docker-compose.dev.yml \
    --env-file .env.development stop dev-gateway 2>/dev/null || true

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
    --env-file .env.development run --rm dev-gateway alembic upgrade head

  say "Recreating dev gateway and console"
  # --remove-orphans: a container still running under an old service name (e.g. before a
  # docker-compose.dev.yml service rename) keeps its container_name, so Compose's own container
  # is blocked from claiming that name -- this clears it instead of failing with a name conflict.
  docker compose -p aura-dev -f docker-compose.dev.yml \
    --env-file .env.development up -d --force-recreate --remove-orphans --no-deps dev-gateway console

  say "Waiting for dev gateway health"
  for attempt in $(seq 1 36); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' aura-dev-gateway 2>/dev/null || echo missing)"
    if [ "$status" = "healthy" ]; then
      info "gateway healthy"
      break
    fi
    if [ "$attempt" = "36" ]; then
      docker compose -p aura-dev -f docker-compose.dev.yml \
        --env-file .env.development logs --tail=40 dev-gateway || true
      die "aura-dev-gateway not healthy (status=$status)"
    fi
    sleep 5
  done

  say "Starting aura-dev app layer (console + workers)"
  docker compose -p aura-dev -f docker-compose.dev.yml \
    --env-file .env.development --profile training up -d

  say "Waiting for dev console health"
  for attempt in $(seq 1 24); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' aura-dev-console 2>/dev/null || echo missing)"
    if [ "$status" = "healthy" ]; then
      info "console healthy"
      break
    fi
    if [ "$attempt" = "24" ]; then
      docker compose -p aura-dev -f docker-compose.dev.yml \
        --env-file .env.development logs --tail=40 console || true
      die "aura-dev-console not healthy (status=$status)"
    fi
    sleep 5
  done
fi

if [ "$SEED" = "1" ]; then
  say "Seeding dev console (Loop B)"
  chmod +x scripts/seed-dev.sh
  if ! ./scripts/seed-dev.sh; then
    info "WARN: dev seed failed — gateway and console stay up; re-run seed-dev.sh on VPS"
  fi
fi

say "Dev health check"
curl -fsSk "https://dev.169.58.188.43.nip.io/health/ready" | head -c 500 || die "dev /health/ready failed"
echo ""
