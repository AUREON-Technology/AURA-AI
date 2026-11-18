# Backup image for the Railway cron service that backs up the LIVE production data (Railway
# Postgres and Redis, Qdrant Cloud) to Backblaze B2 + Cloudflare R2.
# Plan: docs/plans/distributed_storage_plan.md (decisions 8-9). Setup: docs/deployment/backups.md.
#
# Build context is deploy/ (Railway: Root Directory "deploy", RAILWAY_DOCKERFILE_PATH
# "backup/railway.Dockerfile"), so the scripts can be copied in: unlike the VPS sidecar
# (deploy/backup/Dockerfile), a Railway service has no bind mount to read them from.
#
# PG_MAJOR must be >= the Railway Postgres server's major version: pg_dump refuses to dump a newer
# server. The production server reported 18.6 on 2026-11-17, hence the default. Raise it (here, or
# with a Railway service variable named PG_MAJOR, which Railway passes to this ARG) BEFORE that
# server is upgraded, or the nightly dump fails. The package line matches
# deploy/backup/Dockerfile (pinned by deploy/scripts/tests/test_backup_crypto.sh).
ARG PG_MAJOR=18
FROM postgres:${PG_MAJOR}-alpine3.24
RUN apk add --no-cache rclone ca-certificates wget redis 'age~1.3' \
    && age --version
COPY scripts/backup.sh scripts/backup-lib.sh scripts/backup-fetch.sh scripts/backup-open.sh \
     scripts/restore.sh scripts/restore-drill.sh /scripts/
# One cycle per container start; the cron schedule starts it. Metrics have no scraper here, so
# they go to a throwaway path; BACKUP_HEARTBEAT_URL is the failure signal instead.
ENV BACKUP_RUN_ONCE=1 \
    BACKUP_DIR=/backups \
    BACKUP_METRICS_DIR=/tmp/backup-metrics
ENTRYPOINT ["/bin/sh", "/scripts/backup.sh"]
