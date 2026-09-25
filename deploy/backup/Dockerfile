# Alpine branch pinned (3.24) so the `age` constraint below resolves against a known package
# set; Postgres major stays 16 to match the `postgres` service (pg_dump must not be older than the
# server). Bump both deliberately, not by a floating tag moving underneath a deploy.
FROM postgres:16-alpine3.24
# rclone is required for Phase 8 offsite copy (AURA-016). wget/redis-cli are
# used by the local Qdrant/Redis steps. age encrypts every backup artifact to an
# offline-held key before it is written to /backups or shipped offsite (P1.3);
# only the PUBLIC recipient is ever given to this container. Do not bake
# credentials or keys into the image.
RUN apk add --no-cache rclone ca-certificates wget redis 'age~1.3' \
    && age --version
