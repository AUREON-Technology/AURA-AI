#!/usr/bin/env bash
# GitHub Actions entry point. Same as vps-update.sh, with the production
# checkout's locations filled in so the workflow does not have to know them.
#
# Runs vps-update.sh from the *tested SHA*, not from the host working tree.
# Auto-deploy used to exec /opt/aura-stack/deploy/scripts/vps-update.sh on disk,
# which is the previous release -- so a fix in this directory could not take
# effect until a later, already-working deploy. Fetch has already run (the
# workflow fetches origin/main before invoking this file).
#
# Optional environment:
#   AURA_SHA          commit CI tested (must be on origin/main)
#   AURA_HEALTH_URL   override; otherwise https://$AURA_DOMAIN/health/live
#   AURA_READY_TIMEOUT_S  how long vps-update.sh waits for /health/ready (default 300)
#   AURA_STASH_DIRTY  default 1 -- stash tracked local edits so ff-only can proceed
set -euo pipefail

# F47: a full lowercase hex SHA or unset. It selects the vps-update.sh this script runs as root,
# so nothing else -- a branch name, a short SHA, shell syntax -- gets as far as git.
if [ -n "${AURA_SHA:-}" ] && ! [[ "$AURA_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ci-deploy.sh: AURA_SHA must be a full 40-character lowercase hex commit SHA" >&2
  exit 2
fi

export AURA_REPO="${AURA_REPO:-/opt/aura-stack}"
export AURA_COMPOSE="${AURA_COMPOSE:-$AURA_REPO/deploy/docker-compose.prod.yml}"
export AURA_PROJECT="${AURA_PROJECT:-aura}"
export AURA_BACKEND_SERVICE="${AURA_BACKEND_SERVICE:-gateway}"
export AURA_STASH_DIRTY="${AURA_STASH_DIRTY:-1}"
# Set AURA_DEV_OPS=1 only for manual dev TLS/stack/seed bring-up (Deploy VPS workflow_dispatch).
export AURA_DEV_OPS="${AURA_DEV_OPS:-0}"

if [ -z "${AURA_HEALTH_URL:-}" ] && [ -f "$AURA_REPO/deploy/.env" ]; then
  domain="$(sed -n 's/^AURA_DOMAIN=//p' "$AURA_REPO/deploy/.env" | head -1 | tr -d "[:space:]\"'")"
  if [ -n "$domain" ]; then
    # /health/live, not /health (audit F-25). nginx has no `/health` route: it answers that path
    # with a 301 to `/health/`, which `curl -f` counts as success, so the old URL "passed" as
    # long as nginx was up -- whatever the gateway was doing.
    export AURA_HEALTH_URL="https://${domain}/health/live"
  fi
fi

UPDATE_SH="$AURA_REPO/deploy/scripts/vps-update.sh"
if [ -n "${AURA_SHA:-}" ] && git -C "$AURA_REPO" cat-file -e "${AURA_SHA}:deploy/scripts/vps-update.sh" 2>/dev/null; then
  # A private directory, not a fixed /tmp path another local user could pre-create or swap
  # between the write and the run (F47).
  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "$WORK_DIR"' EXIT
  git -C "$AURA_REPO" show "${AURA_SHA}:deploy/scripts/vps-update.sh" > "$WORK_DIR/vps-update.sh"
  UPDATE_SH="$WORK_DIR/vps-update.sh"
fi

cd "$AURA_REPO/deploy"
bash "$UPDATE_SH" "$@"

if [ "${AURA_DEV_OPS:-0}" = "1" ] && [ -f "$AURA_REPO/deploy/scripts/vps-dev-bring-up.sh" ]; then
  bash "$AURA_REPO/deploy/scripts/vps-dev-bring-up.sh"
fi
# Keep dev /console reachable after prod deploy (nginx proxies to aura-dev-console).
if [ -f "$AURA_REPO/deploy/scripts/dev-console-up.sh" ]; then
  bash "$AURA_REPO/deploy/scripts/dev-console-up.sh" || true
fi
