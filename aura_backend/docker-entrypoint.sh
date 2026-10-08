#!/bin/sh
# Wipe Prometheus multiprocess mmap files from a previous PID namespace, then exec CMD.
#
# PROMETHEUS_MULTIPROC_DIR is shared by every uvicorn worker in this container.
# Leftover *.db files from a previous start would double-count counters after PIDs recycle.
set -eu
if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
    mkdir -p "${PROMETHEUS_MULTIPROC_DIR}"
    find "${PROMETHEUS_MULTIPROC_DIR}" -type f -name '*.db' -delete 2>/dev/null || true
fi
exec "$@"
