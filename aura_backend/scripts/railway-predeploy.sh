#!/bin/sh
# Railway pre-deploy step: bring the production database to the image's Alembic head before the
# new deployment takes traffic. railway.json points `preDeployCommand` at this file.
#
# Why a script and not an inline `a && b || c` chain (CB-43, found 2026-10-30): Railway does not
# run `preDeployCommand` through a shell. With the old inline chain every deploy logged
# `=== MIGRATION START ===` and then `Starting Container` 0.2 s later -- the echo ran, nothing
# after it did, and the step still counted as passed. So `alembic upgrade head` never ran on
# deploy, and production sat at 0013 while the code expected 0015. A single
# `sh <script>` argv works whether Railway execs the string or hands it to a shell, and the
# chaining lives here, where a real shell evaluates it.
set -eu

cd "$(dirname "$0")/.."

# Below railway.json's preDeployTimeoutSeconds (300), so a migration stuck on a lock held by
# the still-running old deployment ends with a FAILED marker and a non-zero exit, instead of
# Railway killing the step with nothing in the log.
timeout_s="${AURA_MIGRATION_TIMEOUT_S:-240}"

echo "=== MIGRATION START ==="
echo "revision before: $(alembic current 2>/dev/null | tail -n 1 || echo unknown)"

status=0
timeout "$timeout_s" alembic upgrade head || status=$?
if [ "$status" -ne 0 ]; then
    if [ "$status" -eq 124 ]; then
        echo "=== MIGRATION FAILED (timed out after ${timeout_s}s; check for locks held by the running deployment) ==="
    else
        echo "=== MIGRATION FAILED (alembic exit ${status}) ==="
    fi
    exit 1
fi

echo "revision after: $(alembic current 2>/dev/null | tail -n 1 || echo unknown)"
echo "=== MIGRATION OK ==="
