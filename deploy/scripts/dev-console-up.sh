#!/usr/bin/env bash
# Start only dev gateway + console (fixes nginx 502 on /console when aura-dev-console is down).
set -euo pipefail
REPO="${AURA_REPO:-/opt/aura-stack}"
DEPLOY="${AURA_DEPLOY_DIR:-$REPO/deploy}"
cd "$DEPLOY"
docker compose -p aura-dev -f docker-compose.dev.yml \
  --env-file .env.development up -d gateway console
echo "Waiting for aura-dev-console..."
for attempt in $(seq 1 24); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' aura-dev-console 2>/dev/null || echo missing)"
  if [ "$status" = "healthy" ]; then
    echo "console healthy"
    exit 0
  fi
  sleep 5
done
docker compose -p aura-dev -f docker-compose.dev.yml --env-file .env.development logs --tail=40 console || true
exit 1