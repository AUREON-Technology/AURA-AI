#!/bin/sh
# Nightly backup of every stateful store, with verification.
#
# An unverified backup is a hope, not a backup: this restores the dump header and counts
# tables before declaring success, because the failure mode that matters is discovering at
# recovery time that the dumps were empty for three months.
#
# Runs as a long-lived container that sleeps between cycles rather than as a cron entry, so
# its failures appear in `docker compose logs backup` alongside everything else.
#
# Postgres custom dumps, Qdrant snapshot *files* (copied into $BACKUP_DIR, not left only on
# the Qdrant volume), and a Redis RDB live here. After a successful local cycle, rclone
# copies $BACKUP_DIR to BACKUP_S3_URI when that env is set (credentials from secrets).
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}
INTERVAL_SECONDS=${BACKUP_INTERVAL_SECONDS:-86400}
QDRANT_URL=${QDRANT_URL:-http://qdrant:6333}

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [backup] $*"; }

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
    dest="$BACKUP_DIR/qdrant-$stamp"
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
    target="$BACKUP_DIR/redis-$stamp.rdb"
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

prune() {
    log "pruning backups older than ${RETENTION_DAYS}d"
    find "$BACKUP_DIR" -name 'postgres-*.dump' -type f -mtime "+$RETENTION_DAYS" -print -delete || true
    find "$BACKUP_DIR" -name 'redis-*.rdb' -type f -mtime "+$RETENTION_DAYS" -print -delete || true
    find "$BACKUP_DIR" -type d -name 'qdrant-*' -mtime "+$RETENTION_DAYS" -prune -exec rm -rf {} + || true
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
    metrics_dir="${BACKUP_METRICS_DIR:-/textfile}"
    mkdir -p "$metrics_dir"
    last=$(cat "$BACKUP_DIR/.last_success" 2>/dev/null || echo 0)
    offsite_last=$(cat "$BACKUP_DIR/.last_offsite_success" 2>/dev/null || echo 0)
    tmp="$metrics_dir/aura_backup.prom.$$"
    cat > "$tmp" <<EOF
# HELP aura_backup_last_success_timestamp_seconds Unix time of last verified local backup.
# TYPE aura_backup_last_success_timestamp_seconds gauge
aura_backup_last_success_timestamp_seconds $last
# HELP aura_backup_offsite_configured 1 when BACKUP_S3_URI is set.
# TYPE aura_backup_offsite_configured gauge
aura_backup_offsite_configured $offsite_configured
# HELP aura_backup_offsite_last_success_timestamp_seconds Unix time of last successful rclone copy.
# TYPE aura_backup_offsite_last_success_timestamp_seconds gauge
aura_backup_offsite_last_success_timestamp_seconds $offsite_last
# HELP aura_backup_offsite_last_exit_code Last rclone exit code (0 if offsite is not configured).
# TYPE aura_backup_offsite_last_exit_code gauge
aura_backup_offsite_last_exit_code $offsite_exit
EOF
    mv "$tmp" "$metrics_dir/aura_backup.prom"
}

copy_offsite() {
    uri="${BACKUP_S3_URI:-}"
    if [ -z "$uri" ]; then
        return 0
    fi
    if ! command -v rclone >/dev/null 2>&1; then
        log "ERROR: BACKUP_S3_URI is set but rclone is not installed"
        return 1
    fi

    config="${RCLONE_CONFIG_FILE:-}"
    if [ -n "$config" ] && [ -f "$config" ]; then
        export RCLONE_CONFIG="$config"
    else
        key=$(read_optional_file "${BACKUP_S3_ACCESS_KEY_FILE:-}")
        secret=$(read_optional_file "${BACKUP_S3_SECRET_KEY_FILE:-}")
        if [ -z "$key" ]; then
            key="${AWS_ACCESS_KEY_ID:-}"
        fi
        if [ -z "$secret" ]; then
            secret="${AWS_SECRET_ACCESS_KEY:-}"
        fi
        if [ -z "$key" ] || [ -z "$secret" ]; then
            log "ERROR: offsite copy needs rclone.conf or backup_s3_access_key + backup_s3_secret_key"
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
            auraoffsite:*) dest="$uri" ;;
            s3:*) dest="auraoffsite:${uri#s3:}" ;;
            *) dest="auraoffsite:$uri" ;;
        esac
        uri="$dest"
    fi

    log "rclone copy -> $uri"
    if ! rclone copy "$BACKUP_DIR" "$uri"; then
        log "ERROR: rclone copy failed"
        return 1
    fi
    offsite_days=${BACKUP_OFFSITE_RETENTION_DAYS:-$RETENTION_DAYS}
    rclone delete "$uri" --min-age "${offsite_days}d" || true
    log "offsite copy ok (retention ${offsite_days}d)"
}

mkdir -p "$BACKUP_DIR"
ensure_tools

while true; do
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    if backup_postgres "$stamp"; then
        backup_qdrant "$stamp"
        backup_redis "$stamp" || log "WARNING: redis dump failed — postgres/qdrant retained; users may need to re-login after a redis-less restore"
        prune
        # A marker the monitoring stack can alert on if it stops advancing.
        date -u +%s > "$BACKUP_DIR/.last_success"
        offsite_configured=0
        offsite_exit=0
        if [ -n "${BACKUP_S3_URI:-}" ]; then
            offsite_configured=1
            if copy_offsite; then
                date -u +%s > "$BACKUP_DIR/.last_offsite_success"
                offsite_exit=0
            else
                offsite_exit=1
                log "ERROR: offsite copy failed"
            fi
        fi
        write_backup_metrics "$offsite_configured" "$offsite_exit"
        log "cycle complete"
    else
        log "cycle FAILED -- previous backups retained"
        if [ -n "${BACKUP_S3_URI:-}" ]; then
            write_backup_metrics 1 1
        else
            write_backup_metrics 0 0
        fi
    fi
    sleep "$INTERVAL_SECONDS"
done
