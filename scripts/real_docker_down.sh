#!/usr/bin/env bash
# Stop and remove what scripts/real_docker_up.sh started.
set -euo pipefail
DIR="${AURA_REAL_DOCKER_DIR:-/tmp/aura-real-docker}"
if [[ -f "$DIR/dockerd.pid" ]]; then
  kill "$(cat "$DIR/dockerd.pid")" 2>/dev/null || true
  for _ in $(seq 1 15); do kill -0 "$(cat "$DIR/dockerd.pid")" 2>/dev/null || break; sleep 1; done
fi
rm -rf "$DIR"
echo "real-docker environment removed"
