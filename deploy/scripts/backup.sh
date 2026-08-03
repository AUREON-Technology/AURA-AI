#!/bin/sh
# Nightly backup of every stateful store, with verification.
#
# An unverified backup is a hope, not a backup: this restores the dump header and counts
# tables before declaring success, because the failure mode that matters is discovering at
# recovery time that the dumps were empty for three months.
#
# Runs as a long-lived container that sleeps between cycles rather than as a cron entry, so
# its failures appear in `docker compose logs backup` alongside everything else.
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}
INTERVAL_SECONDS=${BACKUP_INTERVAL_SECONDS:-86400}
QDRANT_URL=${QDRANT_URL:-http://qdrant:6333}

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [backup] $*"; }

# PGPASSFILE points at the Docker secret; psql wants the value, not the path.
if [ -f "${PGPASSFILE:-}" ]; then
    PGPASSWORD="$(cat "$PGPASSFILE")"
    export PGPASSWORD
    unset PGPASSFILE
fi

backup_postgres() {
    stamp=$1
    target="$BACKUP_DIR/postgres-$stamp.dump"
    log "dumping postgres -> $target"
    # Custom format: parallel restore, selective table recovery, and built-in compression.
    pg_dump --format=custom --compress=6 --file="$target"

    # Verify the archive is readable and non-trivial before counting it as a backup.
    if ! pg_restore --list "$target" >/dev/null 2>&1; then
        log "ERROR: dump is unreadable; removing"
        rm -f "$target"
        return 1
    fi
    tables=$(pg_restore --list "$target" | grep -c 'TABLE DATA' || true)
    if [ "$tables" -lt 1 ]; then
        log "ERROR: dump contains no table data; removing"
        rm -f "$target"
        return 1
    fi
    log "postgres ok: $tables tables, $(du -h "$target" | cut -f1)"
}

backup_qdrant() {
    stamp=$1
    log "snapshotting qdrant"
    key=""
    [ -f /run/secrets/qdrant_api_key ] && key="$(cat /run/secrets/qdrant_api_key)"

    # Qdrant snapshots are per-collection; enumerate rather than assuming a fixed name, since
    # index identity encodes the embedding model and changes on a rebuild (ADR-007).
    collections=$(wget -qO- --header="api-key: $key" "$QDRANT_URL/collections" \
        | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' || true)

    for collection in $collections; do
        if wget -qO- --post-data='' --header="api-key: $key" \
            "$QDRANT_URL/collections/$collection/snapshots" >/dev/null 2>&1; then
            log "qdrant snapshot created: $collection"
        else
            log "WARNING: qdrant snapshot failed for $collection"
        fi
    done
}

prune() {
    log "pruning backups older than ${RETENTION_DAYS}d"
    find "$BACKUP_DIR" -name 'postgres-*.dump' -type f -mtime "+$RETENTION_DAYS" -print -delete || true
}

mkdir -p "$BACKUP_DIR"

while true; do
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    if backup_postgres "$stamp"; then
        backup_qdrant "$stamp"
        prune
        # A marker the monitoring stack can alert on if it stops advancing.
        date -u +%s > "$BACKUP_DIR/.last_success"
        log "cycle complete"
    else
        log "cycle FAILED -- previous backups retained"
    fi
    sleep "$INTERVAL_SECONDS"
done
