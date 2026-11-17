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
#   5. If BACKUP_OFFSITE_TARGETS (or BACKUP_S3_URI) is set: rclone the set to each target, verify
#      each remote copy (rclone check + remote CHECKSUMS comparison), and only then apply that
#      target's offsite retention policy. The cycle counts as durable only when at least
#      BACKUP_OFFSITE_MIN_COPIES targets verify (default 2); fewer reports failure, which fires
#      AuraBackupUnderReplicated.
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

# Offsite targets: whitespace-separated rclone remotes. Several targets must be named in an
# rclone.conf (RCLONE_CONFIG_FILE), one remote per provider. A single target may instead be an
# s3 URI with credentials from BACKUP_S3_ACCESS_KEY_FILE / BACKUP_S3_SECRET_KEY_FILE.
# BACKUP_S3_URI is the pre-multi-target name and still works as a one-target list.
OFFSITE_TARGETS=${BACKUP_OFFSITE_TARGETS:-${BACKUP_S3_URI:-}}
# A cycle counts as durable only when this many targets verify. Defaults to 2: one provider
# losing the data must not be the only copy.
OFFSITE_MIN_COPIES=${BACKUP_OFFSITE_MIN_COPIES:-2}
# Counted with globbing off: a target is split on whitespace, never matched against local files.
OFFSITE_TARGET_COUNT=$(set -f; n=0; for _t in $OFFSITE_TARGETS; do n=$((n + 1)); done; echo "$n")
OFFSITE_VERIFIED=0

# Prints why the offsite configuration cannot give a trustworthy copy count, or nothing when it
# can. Checked before every upload: a miscounted copy would let one provider pass for two.
offsite_config_error() (
    set -f
    case "$OFFSITE_MIN_COPIES" in
        ''|*[!0-9]*)
            echo "BACKUP_OFFSITE_MIN_COPIES='$OFFSITE_MIN_COPIES' is not a whole number"; exit 0 ;;
    esac
    if [ "$OFFSITE_MIN_COPIES" -lt 1 ]; then
        echo "BACKUP_OFFSITE_MIN_COPIES must be at least 1"; exit 0
    fi
    seen=" "
    for target in $OFFSITE_TARGETS; do
        case "$target" in
            *'*'*|*'?'*|*'['*)
                echo "offsite target '$target' contains a glob character"; exit 0 ;;
        esac
        case " $seen " in
            *" $target "*) echo "offsite target '$target' is listed twice"; exit 0 ;;
        esac
        # The rclone remote is the part before the first ':'. An on-the-fly remote (":s3,...:")
        # has no name, so the whole target identifies it.
        case "$target" in
            :*) remote_name=$target ;;
            *:*) remote_name=${target%%:*} ;;
            *) remote_name=$target ;;
        esac
        case "$seen" in
            *" @$remote_name "*)
                echo "offsite targets share the rclone remote '$remote_name'; two buckets behind one remote are not independent copies"
                exit 0 ;;
        esac
        seen="$seen$target @$remote_name "
    done
    exit 0
)

# The required-copies gauge must stay numeric whatever the variable holds, or the textfile
# collector rejects the whole metrics file.
offsite_required_metric() {
    case "$OFFSITE_MIN_COPIES" in
        ''|*[!0-9]*) echo 1 ;;
        *) if [ "$OFFSITE_MIN_COPIES" -lt 1 ]; then echo 1; else echo "$OFFSITE_MIN_COPIES"; fi ;;
    esac
}

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
    if [ -n "${BACKUP_DATABASE_URL:-}" ]; then
        # Hosts without the PG* variables (the Railway cron service) pass one connection URL.
        # libpq wants postgresql://; an SQLAlchemy-style URL carries a driver suffix to drop.
        # The URL holds the password, so it is never logged.
        db_url=$(printf '%s' "$BACKUP_DATABASE_URL" | sed 's|^postgres\(ql\)\{0,1\}+[A-Za-z0-9_]*://|postgresql://|')
        pg_dump --format=custom --compress=6 --dbname="$db_url" --file="$target"
    else
        pg_dump --format=custom --compress=6 --file="$target"
    fi

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
    if [ -f /run/secrets/qdrant_api_key ]; then
        key="$(cat /run/secrets/qdrant_api_key)"
    elif [ -n "${QDRANT_API_KEY:-}" ]; then
        # Hosted Qdrant (Qdrant Cloud) on a host with no secret files: the key comes from env.
        key=$QDRANT_API_KEY
    fi

    if ! command -v wget >/dev/null 2>&1; then
        log "WARNING: wget missing; qdrant snapshots stay on the Qdrant volume only"
        return 0
    fi

    # Qdrant snapshots are per-collection; enumerate rather than assuming a fixed name, since
    # index identity encodes the embedding model and changes on a rebuild (ADR-007).
    # One name per "name" field. A greedy sed over the single-line JSON kept only the LAST
    # collection, so every other collection went unbacked-up without a warning.
    collections=$(wget -qO- --header="api-key: $key" "$QDRANT_URL/collections" \
        | grep -o '"name":"[^"]*"' | sed 's/^"name":"//; s/"$//' || true)

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
        # The copy now lives in the backup set; left on Qdrant, one snapshot per collection per
        # night accumulates on Qdrant's own disk (a hard limit on Qdrant Cloud).
        wget -q -O /dev/null --method=DELETE --header="api-key: $key" \
            "$QDRANT_URL/collections/$collection/snapshots/$snap" \
            || log "WARNING: could not delete qdrant snapshot $snap of $collection"
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
    if [ -n "${BACKUP_REDIS_URL:-}" ]; then
        # One URL (redis://user:pass@host:port) on hosts without the REDIS_* variables. Never logged.
        redis-cli -u "$BACKUP_REDIS_URL" --no-auth-warning --rdb "$target"
    elif [ -n "$pass" ]; then
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
    offsite_verified=${4:-0}
    offsite_required=${5:-0}
    metrics_dir="${BACKUP_METRICS_DIR:-/textfile}"
    mkdir -p "$metrics_dir"
    last=$(cat "$BACKUP_DIR/.last_success" 2>/dev/null || echo 0)
    offsite_last=$(cat "$BACKUP_DIR/.last_offsite_success" 2>/dev/null || echo 0)
    encrypted=0
    [ "$(bk_encryption_mode 2>/dev/null || echo invalid)" = age ] && encrypted=1
    recipients=0
    [ "$encrypted" -eq 1 ] && recipients=$(bk_recipient_count)
    tmp="$metrics_dir/aura_backup.prom.$$"
    cat > "$tmp" <<EOF
# HELP aura_backup_last_success_timestamp_seconds Unix time of last verified local backup set.
# TYPE aura_backup_last_success_timestamp_seconds gauge
aura_backup_last_success_timestamp_seconds $last
# HELP aura_backup_encryption_enabled 1 when backup sets are age-encrypted (BACKUP_ENCRYPTION=age).
# TYPE aura_backup_encryption_enabled gauge
aura_backup_encryption_enabled $encrypted
# HELP aura_backup_age_recipients Distinct age public keys each new set is encrypted to (0 when unencrypted or misconfigured).
# TYPE aura_backup_age_recipients gauge
aura_backup_age_recipients $recipients
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
# HELP aura_backup_offsite_copies_verified Offsite targets whose copy of the last set passed remote verification.
# TYPE aura_backup_offsite_copies_verified gauge
aura_backup_offsite_copies_verified $offsite_verified
# HELP aura_backup_offsite_copies_required Offsite targets that must verify for a set to count as durable (BACKUP_OFFSITE_MIN_COPIES).
# TYPE aura_backup_offsite_copies_required gauge
aura_backup_offsite_copies_required $offsite_required
EOF
    mv "$tmp" "$metrics_dir/aura_backup.prom"
}

# Resolves one offsite target into an rclone remote path, exporting rclone config env as needed.
# Prints the remote base on stdout. $1 is the target as listed in BACKUP_OFFSITE_TARGETS.
resolve_offsite_remote() {
    uri=$1
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

# Uploads and verifies one set to every offsite target, then sets OFFSITE_VERIFIED to how many
# passed. Each target is independent: one failing provider does not stop the others. Returns 0
# only when at least OFFSITE_MIN_COPIES targets verified; a single surviving copy is still
# uploaded, but the cycle reports failure so the under-replication alert fires.
copy_offsite() {
    set_dir=$1
    OFFSITE_VERIFIED=0
    if ! command -v rclone >/dev/null 2>&1; then
        log "ERROR: offsite targets are set but rclone is not installed"
        return 1
    fi
    config_problem=$(offsite_config_error)
    if [ -n "$config_problem" ]; then
        log "ERROR: offsite configuration: $config_problem; nothing uploaded"
        return 1
    fi
    if [ "$OFFSITE_TARGET_COUNT" -gt 1 ] && [ ! -f "${RCLONE_CONFIG_FILE:-}" ]; then
        log "ERROR: several offsite targets need named remotes in RCLONE_CONFIG_FILE (not found)"
        return 1
    fi

    for target in $OFFSITE_TARGETS; do
        # Not via $(...): resolve exports rclone config into this shell.
        if ! resolve_offsite_remote "$target" > "$STAGING/.remote"; then
            log "ERROR: cannot resolve offsite target $target"
            rm -f "$STAGING/.remote"
            continue
        fi
        remote=$(cat "$STAGING/.remote")
        rm -f "$STAGING/.remote"

        if ! bk_offsite_push "$set_dir" "$remote"; then
            log "ERROR: offsite copy to $target did not verify"
            continue
        fi
        OFFSITE_VERIFIED=$((OFFSITE_VERIFIED + 1))

        # Retention runs only after this target's upload verified; its failure is logged, not
        # fatal to the (already verified) copy.
        bk_offsite_prune "$remote" "$(basename "$set_dir")" \
            "$OFFSITE_KEEP_DAILY" "$OFFSITE_KEEP_WEEKLY" "$OFFSITE_KEEP_MONTHLY" \
            || log "WARNING: offsite retention skipped for $target this cycle"
        # Pre-P1.3 cycles copied flat, unencrypted artifacts to the bucket root; let those age out.
        rclone delete "$remote" --min-age "${OFFSITE_KEEP_DAILY}d" \
            --include '/postgres-*.dump' --include '/redis-*.rdb' --include '/qdrant-*/**' \
            --include '/.last_success' --include '/.last_offsite_success' || true
        log "offsite copy ok at $target (keep daily=$OFFSITE_KEEP_DAILY weekly=$OFFSITE_KEEP_WEEKLY monthly=$OFFSITE_KEEP_MONTHLY)"
    done

    if [ "$OFFSITE_VERIFIED" -ge "$OFFSITE_MIN_COPIES" ]; then
        date -u +%s > "$BACKUP_DIR/.last_offsite_success"
        log "offsite: $OFFSITE_VERIFIED of $OFFSITE_TARGET_COUNT target(s) verified (required $OFFSITE_MIN_COPIES)"
        return 0
    fi
    log "ERROR: offsite: only $OFFSITE_VERIFIED of $OFFSITE_TARGET_COUNT target(s) verified; BACKUP_OFFSITE_MIN_COPIES=$OFFSITE_MIN_COPIES not met"
    return 1
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
    elif [ "$(bk_recipient_count)" -lt 2 ]; then
        log "WARNING: this set is decryptable by only ONE age key; losing it loses every backup. Add a second public key to backup_age_recipients.txt (maintenance-guide \"Backup encryption keys\")"
    fi
    return 0
}

# Dead man's switch for a scheduler that no Prometheus watches (the Railway cron service): only a
# durable cycle requests BACKUP_HEARTBEAT_URL, so a monitor that alerts when the pings stop
# catches every kind of failure, including the job never starting. The URL is a credential of
# sorts and is never logged.
send_heartbeat() {
    url=${BACKUP_HEARTBEAT_URL:-}
    [ -n "$url" ] || return 0
    if wget -q -O /dev/null -T 30 "$url" 2>/dev/null; then
        log "heartbeat sent"
    else
        log "WARNING: heartbeat request failed"
    fi
}

# One full cycle: check encryption, back up, seal, prune, ship offsite, write metrics. Returns 0
# only when the local set verified AND, with offsite configured, enough offsite copies verified:
# the status BACKUP_RUN_ONCE hands to its scheduler.
backup_once() {
    offsite_configured=0
    offsite_required=0
    if [ -n "$OFFSITE_TARGETS" ]; then
        offsite_configured=1
        offsite_required=$(offsite_required_metric)
    fi

    if ! bk_require_encryption_config; then
        log "cycle REFUSED -- fix the backup encryption configuration; previous backups retained"
        write_backup_metrics "$offsite_configured" "$offsite_configured" 1 0 "$offsite_required"
        return 1
    fi

    if ! run_cycle; then
        log "cycle FAILED -- previous backups retained, nothing pruned"
        write_backup_metrics "$offsite_configured" "$offsite_configured" 0 0 "$offsite_required"
        return 1
    fi

    # A marker the monitoring stack can alert on if it stops advancing.
    date -u +%s > "$BACKUP_DIR/.last_success"
    if bk_prune_local "$BACKUP_DIR" "$KEEP_DAILY" "$KEEP_WEEKLY" "$KEEP_MONTHLY"; then
        prune_legacy
    fi
    offsite_exit=0
    OFFSITE_VERIFIED=0
    if [ "$offsite_configured" -eq 1 ]; then
        STAGING="$BACKUP_DIR/.staging-offsite"
        mkdir -p "$STAGING"
        if ! copy_offsite "$set_dir"; then
            offsite_exit=1
            log "ERROR: offsite copy or its verification failed"
        fi
        cleanup_staging
    fi
    write_backup_metrics "$offsite_configured" "$offsite_exit" 0 "$OFFSITE_VERIFIED" "$offsite_required"
    if [ "$offsite_exit" -ne 0 ]; then
        log "cycle complete locally, but NOT durable offsite"
        return 1
    fi
    log "cycle complete"
    send_heartbeat
    return 0
}

mkdir -p "$BACKUP_DIR"
ensure_tools

# Hosts without secret files (Railway) pass the whole rclone config as one variable, in the same
# format as deploy/backup/rclone.conf.example. It is written next to the sets, readable only by
# this user (umask 077 above), and a mounted RCLONE_CONFIG_FILE always wins.
if [ -n "${BACKUP_RCLONE_CONFIG:-}" ] && [ ! -f "${RCLONE_CONFIG_FILE:-}" ]; then
    RCLONE_CONFIG_FILE="$BACKUP_DIR/.rclone.conf"
    printf '%s\n' "$BACKUP_RCLONE_CONFIG" > "$RCLONE_CONFIG_FILE"
    export RCLONE_CONFIG_FILE
fi

# BACKUP_RUN_ONCE=1: one cycle, then exit with its status -- for a scheduler that starts the
# container (the Railway cron service). Otherwise: the long-lived VPS sidecar loop.
if [ "${BACKUP_RUN_ONCE:-0}" = 1 ]; then
    if backup_once; then
        exit 0
    fi
    exit 1
fi

while true; do
    backup_once || true
    pause_until_next_cycle
done
