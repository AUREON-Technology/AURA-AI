#!/bin/sh
# Restore Postgres from a backup. Destructive against the target database.
#
# Usage (a backup SET -- the normal case since P1.3):
#   docker compose -f deploy/docker-compose.prod.yml run --rm \
#     -v /path/to/offline/aura-backup.agekey:/run/restore-key/age.key:ro \
#     -e BACKUP_AGE_IDENTITY_FILE=/run/restore-key/age.key \
#     --entrypoint /bin/sh backup /scripts/restore.sh /backups/aura-<stamp>
#
# Legacy (a pre-P1.3 unencrypted flat dump; no manifest, so no integrity check beyond pg_restore):
#   ... --entrypoint /bin/sh backup /scripts/restore.sh /backups/postgres-<stamp>.dump
#
# --entrypoint is required: the `backup` service's own compose entrypoint is
# /scripts/backup.sh, so without this override, `run ... backup /scripts/restore.sh ...`
# silently runs ANOTHER BACKUP CYCLE and never touches restore.sh -- no error, no output
# indicating anything went wrong. `docker compose run` overrides the command, not the
# entrypoint, so the entrypoint script receives restore.sh's path as just another argument.
#
# Before anything touches the database, a set is verified end to end: CHECKSUMS.sha256 over the
# ciphertext (no key needed), then every artifact is decrypted with the OFFLINE private key and
# checked against the decrypted MANIFEST.sha256. Any mismatch aborts with the database untouched.
# Decrypted plaintext lives in a private temp directory and is removed on exit.
#
# Interactive by default (type the database name). For a staging drill:
#   RESTORE_CONFIRM=$PGDATABASE /scripts/restore.sh /backups/aura-<stamp>
# The env value must match PGDATABASE exactly — a boolean --yes is not accepted.
#
# Practise on throwaway Postgres via restore-drill.sh, never against production, first.
set -eu

SOURCE=${1:?usage: restore.sh <backup-set-dir | legacy-dump-file>}

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [restore] $*"; }
# shellcheck source-path=SCRIPTDIR source=backup-lib.sh
. "$SCRIPT_DIR/backup-lib.sh"

umask 077
WORK=""
cleanup() {
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 130' INT

started=$(date +%s)

if [ -d "$SOURCE" ]; then
    WORK=$(mktemp -d)
    echo "verifying and decrypting backup set $SOURCE ..."
    if ! bk_open_set "$SOURCE" "$WORK/plain"; then
        echo "ABORTED: backup set failed verification; the database was not touched." >&2
        exit 1
    fi
    DUMP=$(find "$WORK/plain" -maxdepth 1 -type f -name 'postgres-*.dump' | head -n 1)
    [ -n "$DUMP" ] || { echo "set contains no postgres-*.dump" >&2; exit 1; }
    echo "set verified: every artifact matches its manifest."
elif [ -f "$SOURCE" ]; then
    case "$SOURCE" in
        *.age) echo "pass the whole set directory, not a single .age file (the manifest is what makes it verifiable)" >&2; exit 1 ;;
    esac
    echo "WARNING: $SOURCE is a legacy flat dump -- no manifest, no integrity check beyond pg_restore --list."
    DUMP=$SOURCE
else
    echo "no such backup set or dump: $SOURCE" >&2
    exit 1
fi
verified=$(date +%s)

if [ -f "${PGPASSFILE:-}" ]; then
    PGPASSWORD="$(cat "$PGPASSFILE")"
    export PGPASSWORD
    unset PGPASSFILE
fi

db=${PGDATABASE:-aura}
echo "About to restore $(basename "$DUMP") into $db on ${PGHOST:-postgres}."
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
restore_started=$(date +%s)
# --clean --if-exists so a partial previous restore does not block this one.
pg_restore --clean --if-exists --no-owner --no-privileges --jobs=2 \
    --dbname="$db" "$DUMP"
finished=$(date +%s)

echo "timing: verify+decrypt $((verified - started))s, pg_restore $((finished - restore_started))s (excludes the confirmation prompt)"
echo "done. Rebuild the vector index before serving traffic:"
echo "  POST /api/v3/admin/index/rebuild   (documents are the source of truth; ADR-007)"
echo "Qdrant snapshots and the Redis RDB are in the same set; decrypt them with backup-open.sh."
