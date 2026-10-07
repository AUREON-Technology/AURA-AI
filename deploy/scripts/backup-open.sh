#!/bin/sh
# Verify a backup set, and optionally decrypt it, without restoring anything.
#
#   backup-open.sh --verify <set-dir>              # integrity only; needs NO key
#   BACKUP_AGE_IDENTITY_FILE=/path/key.txt \
#   backup-open.sh <set-dir> <empty-output-dir>    # verify, decrypt, check plaintext manifest
#
# --verify is what to run against a copy pulled back from offsite storage, or on the backup host
# itself, before anyone fetches the private key from offline storage. The decrypt form is how the
# Qdrant snapshots and the Redis RDB in a set are recovered (restore.sh covers Postgres); the
# output directory then holds PLAINTEXT -- delete it when done.
#
# Runs on the host (needs `age` and `sha256sum`) or inside the backup image:
#   docker compose -f deploy/docker-compose.prod.yml run --rm --entrypoint /bin/sh backup \
#     /scripts/backup-open.sh --verify /backups/aura-<stamp>
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [backup-open] $*"; }
# shellcheck source-path=SCRIPTDIR source=backup-lib.sh
. "$SCRIPT_DIR/backup-lib.sh"

usage() { echo "usage: backup-open.sh --verify <set-dir> | backup-open.sh <set-dir> <empty-output-dir>" >&2; exit 2; }

if [ "${1:-}" = "--verify" ]; then
    [ -n "${2:-}" ] || usage
    if bk_verify_set "$2"; then
        echo "OK: $2 is complete and every stored file matches CHECKSUMS.sha256 (encryption=$(bk_set_encryption "$2"))"
        exit 0
    fi
    echo "FAIL: $2 did not verify" >&2
    exit 1
fi

if [ -z "${1:-}" ] || [ -z "${2:-}" ]; then usage; fi
if [ -e "$2" ] && [ -n "$(ls -A "$2" 2>/dev/null)" ]; then
    echo "output directory $2 is not empty; refusing (extra files would fail the manifest check)" >&2
    exit 1
fi
umask 077
if bk_open_set "$1" "$2"; then
    echo "OK: decrypted $1 into $2; plaintext matches the set's manifest"
    exit 0
fi
echo "FAIL: $1 could not be opened and verified; nothing usable was written" >&2
exit 1
