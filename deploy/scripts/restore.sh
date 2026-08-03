#!/bin/sh
# Restore from a backup. Deliberately interactive and deliberately not automated.
#
# Usage:  docker compose -f deploy/docker-compose.prod.yml run --rm \
#           -v ./backups:/backups backup /scripts/restore.sh /backups/postgres-<stamp>.dump
#
# Restoring is destructive and irreversible in the direction that matters, so this requires a
# typed confirmation rather than a flag. Practise it on a staging copy before you need it:
# a restore procedure that has never been run is not a recovery plan.
set -eu

DUMP=${1:?usage: restore.sh <dump-file>}
[ -f "$DUMP" ] || { echo "no such dump: $DUMP" >&2; exit 1; }

if [ -f "${PGPASSFILE:-}" ]; then
    PGPASSWORD="$(cat "$PGPASSFILE")"
    export PGPASSWORD
    unset PGPASSFILE
fi

echo "About to restore $DUMP into ${PGDATABASE:-aura} on ${PGHOST:-postgres}."
echo "This REPLACES current data. Stop the gateway and workers first."
printf 'Type the database name to confirm: '
read -r confirmation
[ "$confirmation" = "${PGDATABASE:-aura}" ] || { echo "aborted"; exit 1; }

echo "verifying archive..."
pg_restore --list "$DUMP" >/dev/null

echo "restoring..."
# --clean --if-exists so a partial previous restore does not block this one.
pg_restore --clean --if-exists --no-owner --no-privileges --jobs=2 \
    --dbname="${PGDATABASE:-aura}" "$DUMP"

echo "done. Rebuild the vector index before serving traffic:"
echo "  POST /api/v3/admin/index/rebuild   (documents are the source of truth; ADR-007)"
