FROM postgres:16-alpine
# rclone is required for Phase 8 offsite copy (AURA-016). wget/redis-cli are
# used by the local Qdrant/Redis steps. Do not bake credentials into the image.
RUN apk add --no-cache rclone ca-certificates wget redis

