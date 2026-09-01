#!/usr/bin/env bash
# Collect the last N days of AURA VPS logs (docker compose + Loki) into one file.
# Intended to run ON the VPS as root; output path is the first argument.
set -euo pipefail

DAYS="${AURA_LOG_DAYS:-7}"
OUT="${1:-/tmp/aura-vps-logs-${DAYS}d.log}"
COMPOSE="/opt/aura-stack/deploy/docker-compose.prod.yml"
PROJECT="aura"
# docker compose accepts Go duration strings (e.g. 168h), not "7d"
SINCE_HOURS=$((DAYS * 24))
SINCE="${SINCE_HOURS}h"
SINCE_SEC=$((DAYS * 86400))

mkdir -p "$(dirname "$OUT")"

{
  echo "# AURA Contabo VPS logs — last ${DAYS} days"
  echo "# Host: $(hostname -f 2>/dev/null || hostname)"
  echo "# Collected UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -d /opt/aura-stack/.git ]; then
    echo "# Deployed commit: $(git -C /opt/aura-stack rev-parse HEAD 2>/dev/null || echo unknown)"
  fi
  echo "# Sources: docker compose logs (--since ${SINCE}) + Loki via logcli"
  echo ""

  echo "===== DOCKER COMPOSE LOGS (since ${SINCE}) ====="
  docker compose -p "$PROJECT" -f "$COMPOSE" logs --since "$SINCE" --timestamps --no-color 2>&1 || true

  echo ""
  echo "===== LOKI LOGS (since ${DAYS}d / ${SINCE_SEC}s) ====="
  if docker network inspect aura_observability >/dev/null 2>&1; then
    docker run --rm --network aura_observability grafana/logcli:3.0.0 \
      query '{container=~"aura-.*"}' \
      --addr=http://loki:3100 \
      --since="${SINCE_SEC}s" \
      --limit=0 \
      --output=raw \
      --forward 2>&1 || echo "[loki export failed — see docker logs section above]"
  else
    echo "[aura_observability network not found — skipping Loki export]"
  fi
} >"$OUT"

BYTES=$(wc -c <"$OUT" | tr -d ' ')
echo "Wrote ${OUT} (${BYTES} bytes)"
