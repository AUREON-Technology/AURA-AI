#!/usr/bin/env bash
# Start a throwaway Docker daemon and pull the sandbox base image, so the real-Docker isolation tests
# (aura_backend/tests/e2e/test_docker_sandbox_real.py) can run on a machine that has dockerd installed
# but no daemon running (CI runner, a dev box, a cloud sandbox). Needs root (dockerd) and network for
# one image pull. Nothing here touches an existing Docker installation: its own socket, data root and
# exec root live under $AURA_REAL_DOCKER_DIR (default /tmp/aura-real-docker).
#
#   source <(scripts/real_docker_up.sh --env)   # then:
#   AURA_REAL_DOCKER=1 pytest tests/e2e/test_docker_sandbox_real.py
set -euo pipefail

DIR="${AURA_REAL_DOCKER_DIR:-/tmp/aura-real-docker}"
SOCK="$DIR/docker.sock"
# The same digest the sandbox Dockerfile pins, fetched from a mirror that is not rate limited;
# Docker Hub refuses anonymous pulls from shared IPs.
IMAGE="${AURA_REAL_DOCKER_IMAGE:-mirror.gcr.io/library/python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea}"

if [[ "${1:-}" == "--env" ]]; then
  echo "export DOCKER_HOST=unix://$SOCK AURA_REAL_DOCKER=1 AURA_REAL_DOCKER_IMAGE='$IMAGE'"
  exit 0
fi

command -v dockerd >/dev/null || { echo "dockerd is not installed" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || { echo "run as root: dockerd needs it" >&2; exit 1; }
mkdir -p "$DIR"

if ! DOCKER_HOST="unix://$SOCK" docker info >/dev/null 2>&1; then
  nohup dockerd --host="unix://$SOCK" --data-root="$DIR/data" --exec-root="$DIR/exec" \
    --pidfile="$DIR/dockerd.pid" --iptables=false --ip6tables=false --storage-driver=vfs \
    >"$DIR/dockerd.log" 2>&1 &
  for _ in $(seq 1 30); do
    DOCKER_HOST="unix://$SOCK" docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi
DOCKER_HOST="unix://$SOCK" docker info >/dev/null 2>&1 || { echo "dockerd did not start; see $DIR/dockerd.log" >&2; exit 1; }
DOCKER_HOST="unix://$SOCK" docker image inspect "$IMAGE" >/dev/null 2>&1 || DOCKER_HOST="unix://$SOCK" docker pull "$IMAGE"

echo "ready. In your shell:  source <($0 --env)"
echo "then:                  AURA_REAL_DOCKER=1 pytest tests/e2e/test_docker_sandbox_real.py"
