#!/bin/sh
# Railway pre-deploy step: bring the production database to the image's Alembic head before the
# new deployment takes traffic. railway.json points `preDeployCommand` at this file.
#
# Why a script and not an inline `a && b || c` chain (CB-43, found 2026-10-30): Railway does not
# run `preDeployCommand` through a shell. With the old inline chain every deploy logged
# `=== MIGRATION START ===` and nothing after it -- the echo ran, nothing after it did, and the
# step still counted as passed. So `alembic upgrade head` never ran on deploy, and production
# sat at 0013 while the code expected 0015. A single `sh <script>` argv works whether Railway
# execs the string or hands it to a shell, and the chaining lives here, where a real shell
# evaluates it.
#
# Why it is this chatty (2026-10-30, the first deploy of this script, 8bc8d4c): Railway's
# deployment log again showed `=== MIGRATION START ===` and then nothing. Its PRE_DEPLOY_COMMAND
# step reported success and ended within about a second of that line, too soon for `alembic
# current` to even have started Python. Either Railway drops the last lines of a short-lived
# container, or the script stopped there; the log could not say which. So:
#
# * stderr joins stdout and Python is unbuffered, so an alembic error is in the same log as the
#   markers instead of in /dev/null;
# * every marker carries a UTC timestamp, so log-ingestion lag cannot pass for timing;
# * `alembic current` has its own timeout, like the upgrade, so a database that does not answer
#   ends in FAILED rather than in a step Railway kills with nothing logged;
# * on every exit, success or failure, the script waits `AURA_PREDEPLOY_LOG_FLUSH_S` before the
#   container stops, so the final lines reach Railway's log.
#
# The next deploy's log therefore either shows `revision before/after` and a final marker, or
# shows that the script never got past its first line. Both are conclusive.
set -eu

exec 2>&1
export PYTHONUNBUFFERED=1

cd "$(dirname "$0")/.."

# Budget, all inside railway.json's preDeployTimeoutSeconds (300), so a stuck step ends with a
# FAILED marker and a non-zero exit instead of Railway killing it with nothing in the log:
# two `alembic current` calls + the upgrade + the flush pause. tests/unit/test_release_identity.py
# pins the sum.
current_timeout_s="${AURA_MIGRATION_CURRENT_TIMEOUT_S:-30}"
timeout_s="${AURA_MIGRATION_TIMEOUT_S:-200}"
flush_s="${AURA_PREDEPLOY_LOG_FLUSH_S:-5}"

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

# Runs on every exit, including a `set -e` abort, so the last lines are never lost to a container
# that stops before Railway has read them.
trap 'sleep "$flush_s"' EXIT

revision() {
    out=$(timeout "$current_timeout_s" alembic current 2>&1) && {
        echo "$out" | tail -n 1
        return 0
    }
    rc=$?
    if [ "$rc" -eq 124 ]; then
        echo "unknown (alembic current timed out after ${current_timeout_s}s)"
    else
        echo "unknown (alembic current exit $rc: $(echo "$out" | tail -n 1))"
    fi
}

log "=== MIGRATION START ==="
if [ -n "${AURA_DATABASE_URL:-}" ]; then
    log "database url: set"
else
    log "database url: UNSET (alembic will fall back to localhost and fail)"
fi
log "revision before: $(revision)"

status=0
timeout "$timeout_s" alembic upgrade head || status=$?
if [ "$status" -ne 0 ]; then
    if [ "$status" -eq 124 ]; then
        log "=== MIGRATION FAILED (timed out after ${timeout_s}s; check for locks held by the running deployment) ==="
    else
        log "=== MIGRATION FAILED (alembic exit ${status}) ==="
    fi
    exit 1
fi

log "revision after: $(revision)"
log "=== MIGRATION OK ==="
