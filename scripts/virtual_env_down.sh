#!/usr/bin/env bash
# Stop and delete everything scripts/virtual_env_up.sh created (default /tmp/aura_virtual_env).
set -u
DIR="${AURA_VENV_DIR:-/tmp/aura_virtual_env}"
[ -f "$DIR/redis.pid" ] && kill "$(cat "$DIR/redis.pid")" 2>/dev/null
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
[ -d "$DIR/pgdata" ] && [ -n "$PGBIN" ] && runuser -u postgres -- "$PGBIN/pg_ctl" -D "$DIR/pgdata" -m fast stop >/dev/null 2>&1
rm -rf "$DIR"
echo "virtual environment removed"
