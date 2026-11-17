#!/bin/sh
# Fetch one backup set back from offsite, trying each target until a copy verifies.
#
#   backup-fetch.sh <dest-dir> [set-name]
#
# Targets come from BACKUP_OFFSITE_TARGETS (or BACKUP_S3_URI), exactly as backup.sh reads them,
# and are reached through the rclone config in RCLONE_CONFIG_FILE (or RCLONE_CONFIG). With no
# set name, the newest set held by ANY target is fetched -- a target that missed last night's
# upload must not make the restore silently older.
#
# Each candidate copy is checked against its CHECKSUMS.sha256 before it is accepted, which needs
# no key: a truncated, corrupted or incomplete copy at one provider is skipped and the next
# target is tried. Decryption happens later, in restore.sh / backup-open.sh / restore-drill.sh,
# with the offline private key.
#
# Prints the verified set's path on stdout (logs go to stderr) and exits 0; exits 1 when no
# target holds a copy that verifies, leaving nothing behind in <dest-dir>.
#
# Typical use, on the host:
#   set=$(RCLONE_CONFIG_FILE=deploy/secrets/backup/rclone.conf \
#         BACKUP_OFFSITE_TARGETS="b2-aura:aura-backups r2-aura:aura-backups" \
#         ./deploy/scripts/backup-fetch.sh /root/offsite-check)
#   ./deploy/scripts/restore-drill.sh "$set"
set -eu

DEST=${1:?usage: backup-fetch.sh <dest-dir> [set-name]}
WANTED=${2:-}

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [fetch] $*" >&2; }

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=backup-lib.sh
. "$SCRIPT_DIR/backup-lib.sh"

umask 077

TARGETS=${BACKUP_OFFSITE_TARGETS:-${BACKUP_S3_URI:-}}
if [ -z "$TARGETS" ]; then
    log "ERROR: no offsite targets (set BACKUP_OFFSITE_TARGETS or BACKUP_S3_URI)"
    exit 1
fi
case "$TARGETS" in
    *'*'*|*'?'*|*'['*) log "ERROR: an offsite target contains a glob character"; exit 1 ;;
esac
if [ -n "${RCLONE_CONFIG_FILE:-}" ] && [ -f "$RCLONE_CONFIG_FILE" ]; then
    RCLONE_CONFIG=$RCLONE_CONFIG_FILE
    export RCLONE_CONFIG
fi
command -v rclone >/dev/null 2>&1 || { log "ERROR: rclone is not installed"; exit 1; }

if [ -n "$WANTED" ] && ! bk_is_set_name "$WANTED"; then
    log "ERROR: '$WANTED' is not a backup set name (aura-YYYYmmddTHHMMSSZ)"
    exit 1
fi

# Set names held by one target, newest first. A target that cannot be listed yields nothing.
sets_at() (
    listing=$(rclone lsf --dirs-only "$1" 2>/dev/null) || exit 0
    echo "$listing" | sed 's|/$||' | while IFS= read -r d; do
        if bk_is_set_name "$d"; then echo "$d"; fi
    done | LC_ALL=C sort -r
)

if [ -z "$WANTED" ]; then
    for target in $TARGETS; do
        newest=$(sets_at "$target" | head -n 1)
        if [ -z "$newest" ]; then
            log "no backup sets at $target"
            continue
        fi
        log "newest set at $target: $newest"
        # Set names sort chronologically (fixed-width UTC stamps), so the larger name is newer.
        WANTED=$(printf '%s\n%s\n' "$WANTED" "$newest" | sed '/^$/d' | LC_ALL=C sort -r | head -n 1)
    done
    if [ -z "$WANTED" ]; then
        log "ERROR: no target holds any backup set"
        exit 1
    fi
fi

mkdir -p "$DEST"
out="$DEST/$WANTED"
if [ -e "$out" ]; then
    log "ERROR: $out already exists; refusing to overwrite it"
    exit 1
fi

for target in $TARGETS; do
    log "fetching $WANTED from $target"
    if ! rclone copy "$target/$WANTED" "$out" >&2; then
        log "copy from $target failed; trying the next target"
        rm -rf "$out"
        continue
    fi
    if [ ! -f "$out/$BK_FORMAT_FILE" ]; then
        log "$target has no complete copy of $WANTED; trying the next target"
        rm -rf "$out"
        continue
    fi
    if ! bk_verify_set "$out" >&2; then
        log "the copy at $target does not verify; trying the next target"
        rm -rf "$out"
        continue
    fi
    log "verified $WANTED from $target (keyless; decrypt with the offline key to restore)"
    echo "$out"
    exit 0
done

log "ERROR: no target holds a copy of $WANTED that verifies"
log "an older set can be fetched by name: backup-fetch.sh $DEST aura-<stamp>"
exit 1
