#!/bin/sh
# Restore drill against a throwaway Postgres. Never point this at production.
#
# Usage:
#   ./deploy/scripts/restore-drill.sh                         # synthesizes a dump
#   ./deploy/scripts/restore-drill.sh /path/to/postgres-*.dump # restores a real backup copy
#
# Starts an ephemeral postgres:16-alpine container, pg_restore --list, restores into
# an empty database, asserts TABLE DATA count >= 1, then destroys the container.
#
# Qdrant snapshot files and Redis RDB are not applied here (different engines).
# Tick those by hand using the checklist in docs/guides/maintenance-guide.md.
set -eu

DUMP=${1:-}

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

echo "starting throwaway postgres (not the compose 'postgres' service)..."
CID=$(docker run -d --rm \
    -e POSTGRES_USER=drill \
    -e POSTGRES_PASSWORD=drill \
    -e POSTGRES_DB=drill \
    postgres:16-alpine)

cleanup() {
    echo "removing throwaway container $CID"
    docker rm -f "$CID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

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
    [ -f "$DUMP" ] || { echo "no such dump: $DUMP" >&2; exit 1; }
    echo "copying $DUMP into throwaway postgres..."
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
docker exec "$CID" pg_restore --no-owner --no-privileges --dbname=drill_restore "$INNER"

restored=$(docker exec "$CID" psql -U drill -d drill_restore -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")
if [ "${restored:-0}" -lt 1 ]; then
    echo "FAIL: restored database has $restored public tables" >&2
    exit 1
fi

echo "OK: listed $tables TABLE DATA entries, restored $restored public table(s) into throwaway Postgres."
echo "Manual (not this script): copy qdrant-<stamp>/ snapshots back and redis-*.rdb if you are rehearsing a full recovery."
