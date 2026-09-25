#!/bin/sh
# Nightly backup of every stateful store, with verification, encryption and integrity manifests.
#
# An unverified backup is a hope, not a backup: this restores the dump header and counts
# tables before declaring success, because the failure mode that matters is discovering at
# recovery time that the dumps were empty for three months.
#
# Runs as a long-lived container that sleeps between cycles rather than as a cron entry, so
# its failures appear in `docker compose logs backup` alongside everything else.
#
# One cycle (P1.3):
#   1. Refuse to start if encryption is on (the default) but no age recipient is configured --
#      fail closed, never fall back to plaintext.
#   2. Dump Postgres (custom format, verified), Qdrant snapshot files and a Redis RDB into a
#      private staging directory.
#   3. Seal them into $BACKUP_DIR/aura-<stamp>/ (see backup-lib.sh): every artifact encrypted to
#      the age recipient(s), an encrypted SHA-256 manifest of the plaintext, and a plain
#      CHECKSUMS.sha256 over the ciphertext. The staged plaintext is deleted.
#   4. Verify the sealed set; only then advance .last_success and apply GFS retention (never
#      pruning when the newest set fails verification).
#   5. If BACKUP_S3_URI is set: rclone the set offsite, verify the remote copy (rclone check +
#      remote CHECKSUMS comparison), and only then apply the offsite retention policy.
#
# The host holds only the age PUBLIC key. Decryption needs the private key, which is kept
# offline -- see docs/guides/maintenance-guide.md "Backup encryption keys".
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
INTERVAL_SECONDS=${BACKUP_INTERVAL_SECONDS:-86400}
QDRANT_URL=${QDRANT_URL:-http://qdrant:6333}

# Retention (grandfather-father-son). BACKUP_RETENTION_DAYS is the pre-P1.3 knob and still sets
# the daily count when BACKUP_KEEP_DAILY is not given (one cycle per day => same meaning).
KEEP_DAILY=${BACKUP_KEEP_DAILY:-${BACKUP_RETENTION_DAYS:-7}}
KEEP_WEEKLY=${BACKUP_KEEP_WEEKLY:-4}
KEEP_MONTHLY=${BACKUP_KEEP_MONTHLY:-6}
OFFSITE_KEEP_DAILY=${BACKUP_OFFSITE_KEEP_DAILY:-${BACKUP_OFFSITE_RETENTION_DAYS:-$KEEP_DAILY}}
OFFSITE_KEEP_WEEKLY=${BACKUP_OFFSITE_KEEP_WEEKLY:-$KEEP_WEEKLY}
OFFSITE_KEEP_MONTHLY=${BACKUP_OFFSITE_KEEP_MONTHLY:-$KEEP_MONTHLY}
# Legacy flat artifacts (postgres-*.dump etc. written before backup sets existed) are unencrypted;
# they age out on the old day-based rule once a verified set exists.
LEGACY_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [backup] $*"; }

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=backup-lib.sh
. "$SCRIPT_DIR/backup-lib.sh"

# Plaintext never outlives the cycle: staging is 0700 and removed on exit.
umask 077

ensure_tools() {
    need=0
    command -v wget >/dev/null 2>&1 || need=1
    command -v redis-cli >/dev/null 2>&1 || need=1
    if [ "$need" -eq 1 ] && command -v apk >/dev/null 2>&1; then
        log "installing wget and redis-cli"
        apk add --no-cache wget redis >/dev/null
    fi
}

# PGPASSFILE points at the Docker secret; psql wants the value, not the path.
if [ -f "${PGPASSFILE:-}" ]; then
    PGPASSWORD="$(cat "$PGPASSFILE")"
    export PGPASSWORD
    unset PGPASSFILE
fi

backup_postgres() {
    stamp=$1
    target="$STAGING/postgres-$stamp.dump"
    log "dumping postgres -> $target"
    # Custom format: parallel restore, selective table recovery, and built-in compression.
    pg_dump --format=custom --compress=6 --file="$target"

    # Verify the archive is readable and non-trivial before counting it as a backup. This has to
    # happen on the plaintext, before encryption: the host cannot decrypt afterwards.
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
    dest="$STAGING/qdrant-$stamp"
    mkdir -p "$dest"
    log "snapshotting qdrant -> $dest"
    key=""
    [ -f /run/secrets/qdrant_api_key ] && key="$(cat /run/secrets/qdrant_api_key)"

    if ! command -v wget >/dev/null 2>&1; then
        log "WARNING: wget missing; qdrant snapshots stay on the Qdrant volume only"
        return 0
    fi

    # Qdrant snapshots are per-collection; enumerate rather than assuming a fixed name, since
    # index identity encodes the embedding model and changes on a rebuild (ADR-007).
    collections=$(wget -qO- --header="api-key: $key" "$QDRANT_URL/collections" \
        | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' || true)

    if [ -z "$collections" ]; then
        log "qdrant: no collections to snapshot"
        return 0
    fi

    for collection in $collections; do
        resp=$(wget -qO- --post-data='' --header="api-key: $key" \
            "$QDRANT_URL/collections/$collection/snapshots" || true)
        snap=$(printf '%s' "$resp" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -n 1)
        if [ -z "$snap" ]; then
            log "WARNING: qdrant snapshot create failed for $collection"
            continue
        fi
        if wget -qO "$dest/$collection.snapshot" --header="api-key: $key" \
            "$QDRANT_URL/collections/$collection/snapshots/$snap"; then
            log "qdrant snapshot copied: $collection ($snap)"
        else
            log "WARNING: qdrant snapshot download failed for $collection"
            rm -f "$dest/$collection.snapshot"
        fi
    done
}

backup_redis() {
    stamp=$1
    target="$STAGING/redis-$stamp.rdb"
    if ! command -v redis-cli >/dev/null 2>&1; then
        log "WARNING: redis-cli not installed; skipping redis dump"
        return 0
    fi
    pass=""
    if [ -f "${REDIS_PASSWORD_FILE:-}" ]; then
        pass="$(cat "$REDIS_PASSWORD_FILE")"
    elif [ -n "${REDIS_PASSWORD:-}" ]; then
        pass="$REDIS_PASSWORD"
    fi
    host="${REDIS_HOST:-redis}"
    log "dumping redis -> $target"
    # --rdb copies RDB over the wire; the dump.rdb on the redis volume is not mounted here.
    # Refresh tokens live in Redis: a stale RDB means users re-login. Documented in maintenance-guide.
    if [ -n "$pass" ]; then
        redis-cli -h "$host" -a "$pass" --no-auth-warning --rdb "$target"
    else
        redis-cli -h "$host" --rdb "$target"
    fi
    if [ ! -s "$target" ]; then
        log "WARNING: redis dump missing or empty"
        rm -f "$target"
        return 1
    fi
    log "redis ok: $(du -h "$target" | cut -f1)"
}

prune_legacy() {
    # Pre-P1.3 flat artifacts only. Sets (aura-*) are governed by bk_prune_local.
    find "$BACKUP_DIR" -maxdepth 1 -name 'postgres-*.dump' -type f -mtime "+$LEGACY_RETENTION_DAYS" -print -delete || true
    find "$BACKUP_DIR" -maxdepth 1 -name 'redis-*.rdb' -type f -mtime "+$LEGACY_RETENTION_DAYS" -print -delete || true
    find "$BACKUP_DIR" -maxdepth 1 -type d -name 'qdrant-*' -mtime "+$LEGACY_RETENTION_DAYS" -print -prune -exec rm -rf {} + || true
}

read_optional_file() {
    path=$1
    if [ -f "$path" ]; then
        cat "$path"
    fi
}

write_backup_metrics() {
    offsite_configured=$1
    offsite_exit=$2
    config_error=${3:-0}
    metrics_dir="${BACKUP_METRICS_DIR:-/textfile}"
    mkdir -p "$metrics_dir"
    last=$(cat "$BACKUP_DIR/.last_success" 2>/dev/null || echo 0)
    offsite_last=$(cat "$BACKUP_DIR/.last_offsite_success" 2>/dev/null || echo 0)
    encrypted=0
    [ "$(bk_encryption_mode 2>/dev/null || echo invalid)" = age ] && encrypted=1
    tmp="$metrics_dir/aura_backup.prom.$$"
    cat > "$tmp" <<EOF
# HELP aura_backup_last_success_timestamp_seconds Unix time of last verified local backup set.
# TYPE aura_backup_last_success_timestamp_seconds gauge
aura_backup_last_success_timestamp_seconds $last
# HELP aura_backup_encryption_enabled 1 when backup sets are age-encrypted (BACKUP_ENCRYPTION=age).
# TYPE aura_backup_encryption_enabled gauge
aura_backup_encryption_enabled $encrypted
# HELP aura_backup_config_error 1 when the last cycle refused to run on invalid backup configuration.
# TYPE aura_backup_config_error gauge
aura_backup_config_error $config_error
# HELP aura_backup_offsite_configured 1 when BACKUP_S3_URI is set.
# TYPE aura_backup_offsite_configured gauge
aura_backup_offsite_configured $offsite_configured
# HELP aura_backup_offsite_last_success_timestamp_seconds Unix time of last offsite copy that passed remote verification.
# TYPE aura_backup_offsite_last_success_timestamp_seconds gauge
aura_backup_offsite_last_success_timestamp_seconds $offsite_last
# HELP aura_backup_offsite_last_exit_code Last offsite copy+verify exit code (0 if offsite is not configured).
# TYPE aura_backup_offsite_last_exit_code gauge
aura_backup_offsite_last_exit_code $offsite_exit
EOF
    mv "$tmp" "$metrics_dir/aura_backup.prom"
}

# Resolves BACKUP_S3_URI into an rclone remote path, exporting rclone config env as needed.
# Prints the remote base on stdout.
resolve_offsite_remote() {
    uri="${BACKUP_S3_URI:-}"
    config="${RCLONE_CONFIG_FILE:-}"
    if [ -n "$config" ] && [ -f "$config" ]; then
        export RCLONE_CONFIG="$config"
        echo "$uri"
        return 0
    fi
    key=$(read_optional_file "${BACKUP_S3_ACCESS_KEY_FILE:-}")
    secret=$(read_optional_file "${BACKUP_S3_SECRET_KEY_FILE:-}")
    if [ -z "$key" ]; then
        key="${AWS_ACCESS_KEY_ID:-}"
    fi
    if [ -z "$secret" ]; then
        secret="${AWS_SECRET_ACCESS_KEY:-}"
    fi
    if [ -z "$key" ] || [ -z "$secret" ]; then
        log "ERROR: offsite copy needs rclone.conf or backup_s3_access_key + backup_s3_secret_key" >&2
        return 1
    fi
    export RCLONE_CONFIG_AURAOFFSITE_TYPE=s3
    export RCLONE_CONFIG_AURAOFFSITE_PROVIDER="${BACKUP_S3_PROVIDER:-Other}"
    export RCLONE_CONFIG_AURAOFFSITE_ACCESS_KEY_ID="$key"
    export RCLONE_CONFIG_AURAOFFSITE_SECRET_ACCESS_KEY="$secret"
    if [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
        export RCLONE_CONFIG_AURAOFFSITE_ENDPOINT="$BACKUP_S3_ENDPOINT"
    fi
    export RCLONE_CONFIG_AURAOFFSITE_ACL=private
    case "$uri" in
        auraoffsite:*) echo "$uri" ;;
        s3:*) echo "auraoffsite:${uri#s3:}" ;;
        *) echo "auraoffsite:$uri" ;;
    esac
}

copy_offsite() {
    set_dir=$1
    if ! command -v rclone >/dev/null 2>&1; then
        log "ERROR: BACKUP_S3_URI is set but rclone is not installed"
        return 1
    fi
    # Not via $(...): the function exports rclone config into this shell.
    resolve_offsite_remote > "$STAGING/.remote" || return 1
    remote=$(cat "$STAGING/.remote")
    rm -f "$STAGING/.remote"

    bk_offsite_push "$set_dir" "$remote" || return 1
    date -u +%s > "$BACKUP_DIR/.last_offsite_success"

    # Retention runs only after this cycle's upload verified; its failure is logged, not fatal to
    # the (already verified) copy.
    bk_offsite_prune "$remote" "$(basename "$set_dir")" \
        "$OFFSITE_KEEP_DAILY" "$OFFSITE_KEEP_WEEKLY" "$OFFSITE_KEEP_MONTHLY" \
        || log "WARNING: offsite retention skipped this cycle"
    # Pre-P1.3 cycles copied flat, unencrypted artifacts to the bucket root; let those age out.
    rclone delete "$remote" --min-age "${OFFSITE_KEEP_DAILY}d" \
        --include '/postgres-*.dump' --include '/redis-*.rdb' --include '/qdrant-*/**' \
        --include '/.last_success' --include '/.last_offsite_success' || true
    log "offsite copy ok (keep daily=$OFFSITE_KEEP_DAILY weekly=$OFFSITE_KEEP_WEEKLY monthly=$OFFSITE_KEEP_MONTHLY)"
}

cleanup_staging() {
    if [ -n "${STAGING:-}" ] && [ -d "$STAGING" ]; then
        rm -rf "$STAGING"
    fi
}
trap cleanup_staging EXIT

# Sleep in the background and `wait` for it: a shell blocked on a foreground `sleep` defers its
# TERM trap until the sleep ends, so `docker stop` would always fall through to SIGKILL.
pause_until_next_cycle() {
    sleep "$INTERVAL_SECONDS" &
    SLEEP_PID=$!
    wait "$SLEEP_PID" || true
    SLEEP_PID=""
}
SLEEP_PID=""
trap 'cleanup_staging; [ -n "$SLEEP_PID" ] && kill "$SLEEP_PID" 2>/dev/null; exit 143' TERM
trap 'cleanup_staging; exit 130' INT

run_cycle() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    set_dir="$BACKUP_DIR/${BK_SET_PREFIX}$stamp"

    # A crashed earlier cycle may have left plaintext behind.
    for leftover in "$BACKUP_DIR"/.staging-* "$BACKUP_DIR"/.incoming-*; do
        [ -e "$leftover" ] || continue
        log "removing leftover $(basename "$leftover") from an interrupted cycle"
        rm -rf "$leftover"
    done

    STAGING="$BACKUP_DIR/.staging-$stamp"
    mkdir -p "$STAGING"

    if ! backup_postgres "$stamp"; then
        return 1
    fi
    backup_qdrant "$stamp"
    backup_redis "$stamp" || log "WARNING: redis dump failed — postgres/qdrant retained; users may need to re-login after a redis-less restore"
    # Empty qdrant dir (no collections) is not an artifact.
    rmdir "$STAGING/qdrant-$stamp" 2>/dev/null || true

    log "sealing set $(basename "$set_dir") (encryption=$(bk_encryption_mode))"
    if ! bk_seal_set "$STAGING" "$set_dir"; then
        log "ERROR: sealing/verification of $(basename "$set_dir") failed"
        cleanup_staging
        return 1
    fi
    cleanup_staging
    log "set verified: $(basename "$set_dir"), $(du -sh "$set_dir" | cut -f1)"
    if [ "$(bk_encryption_mode)" = none ]; then
        log "WARNING: BACKUP_ENCRYPTION=none -- this set is PLAINTEXT on disk and will not be shipped offsite"
    fi
    return 0
}

mkdir -p "$BACKUP_DIR"
ensure_tools

while true; do
    offsite_configured=0
    [ -n "${BACKUP_S3_URI:-}" ] && offsite_configured=1

    if ! bk_require_encryption_config; then
        log "cycle REFUSED -- fix the backup encryption configuration; previous backups retained"
        write_backup_metrics "$offsite_configured" "$offsite_configured" 1
        pause_until_next_cycle
        continue
    fi

    if run_cycle; then
        # A marker the monitoring stack can alert on if it stops advancing.
        date -u +%s > "$BACKUP_DIR/.last_success"
        if bk_prune_local "$BACKUP_DIR" "$KEEP_DAILY" "$KEEP_WEEKLY" "$KEEP_MONTHLY"; then
            prune_legacy
        fi
        offsite_exit=0
        if [ "$offsite_configured" -eq 1 ]; then
            STAGING="$BACKUP_DIR/.staging-offsite"
            mkdir -p "$STAGING"
            if ! copy_offsite "$set_dir"; then
                offsite_exit=1
                log "ERROR: offsite copy or its verification failed"
            fi
            cleanup_staging
        fi
        write_backup_metrics "$offsite_configured" "$offsite_exit" 0
        log "cycle complete"
    else
        log "cycle FAILED -- previous backups retained, nothing pruned"
        write_backup_metrics "$offsite_configured" "$offsite_configured" 0
    fi
    pause_until_next_cycle
done
