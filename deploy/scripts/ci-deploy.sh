#!/usr/bin/env bash
# GitHub Actions entry point. Same as vps-update.sh, with the production
# checkout's locations filled in so the workflow does not have to know them.
#
# Optional environment:
#   AURA_SHA          commit CI tested (must be on origin/main)
#   AURA_HEALTH_URL   override; otherwise https://$AURA_DOMAIN/health
set -euo pipefail

export AURA_REPO="${AURA_REPO:-/opt/aura-stack}"
export AURA_COMPOSE="${AURA_COMPOSE:-$AURA_REPO/deploy/docker-compose.prod.yml}"
export AURA_PROJECT="${AURA_PROJECT:-aura}"
export AURA_BACKEND_SERVICE="${AURA_BACKEND_SERVICE:-gateway}"

if [ -z "${AURA_HEALTH_URL:-}" ] && [ -f "$AURA_REPO/deploy/.env" ]; then
  domain="$(sed -n 's/^AURA_DOMAIN=//p' "$AURA_REPO/deploy/.env" | head -1 | tr -d "[:space:]\"'")"
  if [ -n "$domain" ]; then
    export AURA_HEALTH_URL="https://${domain}/health"
  fi
fi

cd "$AURA_REPO/deploy"
exec bash "$AURA_REPO/deploy/scripts/vps-update.sh" "$@"
