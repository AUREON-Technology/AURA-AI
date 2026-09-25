#!/bin/sh
# Restore drill against a throwaway Postgres. Never point this at production.
#
# Usage:
#   ./deploy/scripts/restore-drill.sh                          # synthesizes a dump
#   BACKUP_AGE_IDENTITY_FILE=/path/to/offline.agekey \
#   ./deploy/scripts/restore-drill.sh /path/to/aura-<stamp>     # a real (encrypted) backup set
#   ./deploy/scripts/restore-drill.sh /path/to/postgres-*.dump  # a legacy flat dump
#
# For a backup set it first verifies CHECKSUMS.sha256, decrypts with the offline private key and
# checks every artifact (Qdrant/Redis too) against the set's decrypted manifest -- the same path
# restore.sh takes. Then it starts an ephemeral postgres:16-alpine container, pg_restore --list,
# restores into an empty database, asserts TABLE DATA count >= 1 and rows >= 1, then destroys
# the container.
#
# RTO evidence: it prints the wall-clock time of each phase (verify+decrypt, container ready,
# pg_restore, total) and, when DRILL_RECORD is set, appends one tab-separated line to that file:
#   utc_time  source  encryption  bytes  verify_s  restore_s  total_s  tables  rows  result
#
# Qdrant snapshot files and Redis RDB are not applied here (different engines).
# Tick those by hand using the checklist in docs/guides/maintenance-guide.md.
set -eu

SOURCE=${1:-}
DUMP=""
DRILL_RECORD=${DRILL_RECORD:-}
t_start=$(date +%s)

if [ -n "${PGHOST:-}" ]; then
    echo "refusing to run: PGHOST is set to '${PGHOST}'." >&2
    echo "This drill always starts its own throwaway container and must not inherit prod." >&2
    exit 2
fi
if [ -n "${PGDATABASE:-}" ] || [ -n "${PGUSER:-}" ]; then
    echo "refusing to run: PGDATABASE/PGUSER is set. Unset them so this cannot hit prod." >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for the throwaway Postgres" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [drill] $*"; }
# shellcheck source-path=SCRIPTDIR source=backup-lib.sh
. "$SCRIPT_DIR/backup-lib.sh"

CID=""
WORK=""
encryption="synthetic"
bytes=0
verify_s=0
restore_s=0
tables=0
rows=0

record() {
    result=$1
    total_s=$(( $(date +%s) - t_start ))
    echo "timing: verify+decrypt ${verify_s}s, pg_restore ${restore_s}s, total ${total_s}s ($result)"
    if [ -n "$DRILL_RECORD" ]; then
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SOURCE:-synthetic}" "$encryption" "$bytes" \
            "$verify_s" "$restore_s" "$total_s" "$tables" "$rows" "$result" >> "$DRILL_RECORD"
        echo "recorded in $DRILL_RECORD"
    fi
}

cleanup() {
    status=$?
    if [ -n "$CID" ]; then
        echo "removing throwaway container $CID"
        docker rm -f "$CID" >/dev/null 2>&1 || true
    fi
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK"
    fi
    if [ "$status" -ne 0 ]; then
        record FAIL
    fi
}
trap cleanup EXIT

umask 077
if [ -n "$SOURCE" ] && [ -d "$SOURCE" ]; then
    WORK=$(mktemp -d)
    encryption=$(bk_set_encryption "$SOURCE" 2>/dev/null || echo unknown)
    bytes=$(du -sk "$SOURCE" | cut -f1)
    bytes=$((bytes * 1024))
    echo "verifying and decrypting backup set $SOURCE (encryption=$encryption)..."
    t0=$(date +%s)
    if ! bk_open_set "$SOURCE" "$WORK/plain"; then
        echo "FAIL: backup set did not verify / decrypt" >&2
        exit 1
    fi
    verify_s=$(( $(date +%s) - t0 ))
    DUMP=$(find "$WORK/plain" -maxdepth 1 -type f -name 'postgres-*.dump' | head -n 1)
    [ -n "$DUMP" ] || { echo "FAIL: set contains no postgres-*.dump" >&2; exit 1; }
    echo "set verified in ${verify_s}s: every artifact matches its manifest"
elif [ -n "$SOURCE" ]; then
    [ -f "$SOURCE" ] || { echo "no such dump or backup set: $SOURCE" >&2; exit 1; }
    DUMP=$SOURCE
    encryption="legacy-plain"
    bytes=$(wc -c < "$DUMP" | tr -d ' ')
fi

echo "starting throwaway postgres (not the compose 'postgres' service)..."
CID=$(docker run -d --rm \
    -e POSTGRES_USER=drill \
    -e POSTGRES_PASSWORD=drill \
    -e POSTGRES_DB=drill \
    postgres:16-alpine)

i=0
while [ "$i" -lt 40 ]; do
    if docker exec "$CID" pg_isready -U drill -d drill >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 1
done
docker exec "$CID" pg_isready -U drill -d drill >/dev/null

INNER=/tmp/drill.dump
if [ -n "$DUMP" ]; then
    echo "copying $(basename "$DUMP") into throwaway postgres..."
    docker cp "$DUMP" "$CID":"$INNER"
else
    echo "no dump given; synthesizing a one-table archive..."
    docker exec "$CID" psql -U drill -d drill -c \
        'CREATE TABLE drill_probe (id int PRIMARY KEY); INSERT INTO drill_probe VALUES (1);'
    docker exec "$CID" pg_dump -U drill -d drill --format=custom -f "$INNER"
fi

echo "pg_restore --list:"
docker exec "$CID" pg_restore --list "$INNER"

tables=$(docker exec "$CID" pg_restore --list "$INNER" | grep -c 'TABLE DATA' || true)
if [ "$tables" -lt 1 ]; then
    echo "FAIL: archive contains $tables TABLE DATA entries" >&2
    exit 1
fi

echo "creating empty database drill_restore and restoring..."
docker exec "$CID" psql -U drill -d postgres -c 'CREATE DATABASE drill_restore;'
t0=$(date +%s)
docker exec "$CID" pg_restore -U drill --no-owner --no-privileges --dbname=drill_restore "$INNER"
restore_s=$(( $(date +%s) - t0 ))

restored=$(docker exec "$CID" psql -U drill -d drill_restore -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")
if [ "${restored:-0}" -lt 1 ]; then
    echo "FAIL: restored database has $restored public tables" >&2
    exit 1
fi

# Counting tables is not enough: an archive whose every COPY silently restored nothing still
# leaves the full schema behind, so the check above passes while the backup is worthless. Count
# the actual rows. query_to_xml is the portable way to run count(*) over a dynamic table list
# without a DO block or a second round trip.
rows=$(docker exec "$CID" psql -U drill -d drill_restore -Atc "
    SELECT coalesce(sum(n), 0) FROM (
        SELECT (xpath('/row/c/text()',
                      query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
                                   false, true, '')))[1]::text::bigint AS n
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ) counts;")
if [ "${rows:-0}" -lt 1 ]; then
    echo "FAIL: restored $restored table(s) but 0 rows -- the schema came back and the data did not." >&2
    echo "If you are deliberately drilling a backup of an empty database, that is the one case" >&2
    echo "where this failure is expected rather than a broken backup." >&2
    exit 1
fi

record OK
echo "OK: listed $tables TABLE DATA entries, restored $restored public table(s) and $rows row(s) into throwaway Postgres."
echo "Manual (not this script): decrypt the set with backup-open.sh and restore qdrant-<stamp>/ snapshots and redis-*.rdb if you are rehearsing a full recovery."
