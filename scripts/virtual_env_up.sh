#!/usr/bin/env bash
# Stand up the "virtual environment" the gated live tests need (tests/e2e/test_incomplete_tools_live_env.py).
#
# Everything lives OUTSIDE the repository (default /tmp/aura_virtual_env) and nothing here touches the
# product's dependency locks. It needs: redis-server, the PostgreSQL server binaries (apt install postgresql),
# ffmpeg, python3 with pip (only to fetch Playwright for the test interpreter's Python version), and network
# access to the pip registry and the Playwright CDN. Each service is optional: whatever is missing is
# skipped with a message, and the matching live tests skip with the same reason.
#
#   scripts/virtual_env_up.sh                # start everything, write $AURA_VENV_DIR/env
#   source /tmp/aura_virtual_env/env         # export the AURA_VENV_* variables
#   AURA_VIRTUAL_ENV=1 pytest tests/e2e/test_incomplete_tools_live_env.py
#   scripts/virtual_env_down.sh              # stop and delete it all
set -u
DIR="${AURA_VENV_DIR:-/tmp/aura_virtual_env}"
PY="${AURA_VENV_TEST_PYTHON:-python3}"        # the interpreter that runs pytest (Playwright must match its version)
REDIS_PORT="${AURA_VENV_REDIS_PORT:-16379}"
PG_PORT="${AURA_VENV_PG_PORT:-15432}"
mkdir -p "$DIR" && chmod 755 "$DIR"
ENVFILE="$DIR/env"
: > "$ENVFILE"
echo "export AURA_VIRTUAL_ENV=1" >> "$ENVFILE"

# --- Redis (real server, loopback, no persistence) ------------------------------------------------
if command -v redis-server >/dev/null 2>&1; then
  redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --save "" --appendonly no --daemonize yes \
    --pidfile "$DIR/redis.pid" --dir "$DIR" >/dev/null
  echo "export AURA_VENV_REDIS_URL=redis://127.0.0.1:$REDIS_PORT/0" >> "$ENVFILE"
  echo "redis: up on 127.0.0.1:$REDIS_PORT"
else
  echo "redis: redis-server not installed (apt install redis-server) - live Redis tests will skip"
fi

# --- PostgreSQL (real server, unix socket + loopback, own data dir, run as the postgres user) -------
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
if [ -n "$PGBIN" ] && id postgres >/dev/null 2>&1; then
  PGDATA="$DIR/pgdata"
  if [ ! -d "$PGDATA" ]; then
    mkdir -p "$PGDATA" && chown postgres "$PGDATA"
    runuser -u postgres -- "$PGBIN/initdb" -D "$PGDATA" -A trust >/dev/null
  fi
  mkdir -p "$DIR/pgsock" && chown postgres "$DIR/pgsock"
  runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -l "$DIR/pgsock/pg.log" -w \
    -o "-p $PG_PORT -k $DIR/pgsock -c listen_addresses=127.0.0.1" start >/dev/null \
  && { runuser -u postgres -- "$PGBIN/createdb" -h 127.0.0.1 -p "$PG_PORT" aura_virtual 2>/dev/null || true; \
       echo "export AURA_VENV_PG_DSN=postgresql://postgres@127.0.0.1:$PG_PORT/aura_virtual" >> "$ENVFILE"; \
       echo "postgres: up on 127.0.0.1:$PG_PORT (db aura_virtual)"; } \
  || echo "postgres: failed to start (see $DIR/pgsock/pg.log)"
else
  echo "postgres: server binaries or the postgres user are missing (apt install postgresql) - live Postgres tests will skip"
fi

# --- Playwright + Chromium for the test interpreter's Python version, installed outside the repo ----
SITE="$DIR/site"
PYVER="$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
if [ ! -d "$SITE/playwright" ]; then
  python3 -m pip install -q --target "$SITE" --python-version "$PYVER" --only-binary=:all: --implementation cp \
    playwright greenlet pyee typing_extensions >/dev/null 2>&1 || echo "playwright: pip install failed"
fi
if [ -d "$SITE/playwright" ]; then
  PYTHONPATH="$SITE" PLAYWRIGHT_BROWSERS_PATH="$DIR/browsers" "$PY" -m playwright install chromium >/dev/null 2>&1 \
    && { echo "export AURA_VENV_PLAYWRIGHT_SITE=$SITE" >> "$ENVFILE"; echo "export PLAYWRIGHT_BROWSERS_PATH=$DIR/browsers" >> "$ENVFILE"; echo "playwright: chromium ready"; } \
    || echo "playwright: chromium download failed - browser tests will skip"
fi

# --- ffmpeg ---------------------------------------------------------------------------------------
command -v ffmpeg >/dev/null 2>&1 && echo "export AURA_VENV_FFMPEG=$(command -v ffmpeg)" >> "$ENVFILE" || echo "ffmpeg: not installed"
echo "wrote $ENVFILE"
