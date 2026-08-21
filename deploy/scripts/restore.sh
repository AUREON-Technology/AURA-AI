#!/bin/sh
# Restore from a backup. Destructive against the target database.
#
# Usage:  docker compose -f deploy/docker-compose.prod.yml run --rm \
#           -v ./backups:/backups backup /scripts/restore.sh /backups/postgres-<stamp>.dump
#
# Interactive by default (type the database name). For a staging drill:
#   RESTORE_CONFIRM=$PGDATABASE /scripts/restore.sh /backups/postgres-<stamp>.dump
# The env value must match PGDATABASE exactly — a boolean --yes is not accepted.
#
# Practise on throwaway Postgres via restore-drill.sh, never against production, first.
set -eu

DUMP=${1:?usage: restore.sh <dump-file>}
[ -f "$DUMP" ] || { echo "no such dump: $DUMP" >&2; exit 1; }

if [ -f "${PGPASSFILE:-}" ]; then
    PGPASSWORD="$(cat "$PGPASSFILE")"
    export PGPASSWORD
    unset PGPASSFILE
fi

db=${PGDATABASE:-aura}
echo "About to restore $DUMP into $db on ${PGHOST:-postgres}."
echo "This REPLACES current data. Stop the gateway and workers first."

if [ -n "${RESTORE_CONFIRM:-}" ]; then
    confirmation=$RESTORE_CONFIRM
    if [ "$confirmation" != "$db" ]; then
        echo "aborted: RESTORE_CONFIRM='$confirmation' does not match PGDATABASE='$db'" >&2
        exit 1
    fi
    echo "RESTORE_CONFIRM matches $db; continuing non-interactively."
else
    printf 'Type the database name to confirm: '
    read -r confirmation
    [ "$confirmation" = "$db" ] || { echo "aborted"; exit 1; }
fi

echo "verifying archive..."
pg_restore --list "$DUMP" >/dev/null

echo "restoring..."
# --clean --if-exists so a partial previous restore does not block this one.
pg_restore --clean --if-exists --no-owner --no-privileges --jobs=2 \
    --dbname="$db" "$DUMP"

echo "done. Rebuild the vector index before serving traffic:"
echo "  POST /api/v3/admin/index/rebuild   (documents are the source of truth; ADR-007)"
