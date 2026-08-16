#!/usr/bin/env bash
#
# Capture everything a host migration could destroy, before the migration.
#
# Run this on the OLD host, before triggering a VPS resize or provisioning a new server. It
# implements Phases 1 and 2 of docs/guides/host-migration-runbook.md as one command, because a
# backup that is a wall of copy-paste is a backup that gets half-done under time pressure.
#
# It is read-only against the running system. Nothing is stopped, deleted or reconfigured. The
# only writes are into the output directory.
#
# THE THREE THINGS THIS EXISTS FOR, none of which live where you would expect:
#
#   1. The vector index. `services/rag.py` falls back to QdrantClient(path="local_qdrant_storage")
#      -- a RELATIVE path. Unless a volume is mounted over it, the whole index sits in the
#      container's writable layer and dies on the next `docker compose up --force-recreate`.
#   2. The learning corpus. `settings.data_dir` defaults to ".", so learning_logs.json and the
#      compiled datasets are written inside the image, with the same fate.
#   3. Which Postgres is which. There may be a host-level instance AND a containerised one. Moving
#      the wrong one, or assuming they are the same, loses data quietly.
#
# Usage:
#   ./pre-migration-backup.sh [output-dir]        # default /root/aura-migration
#
# Exit codes: 0 all good; 1 a required step failed; 2 finished with warnings worth reading.

set -uo pipefail   # deliberately NOT -e: one missing component must not abandon the rest

OUT="${1:-/root/aura-migration}"
WARNINGS=0

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[1;33mWARN\033[0m %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
die()  { printf '\n\033[1;31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found; run this on the host, as root"

mkdir -p "$OUT" || die "cannot create $OUT"
cd "$OUT" || die "cannot enter $OUT"
# Resolve to absolute: the scp hint printed at the end is useless if it is relative, because it
# would resolve against the remote $HOME rather than where the backup actually is.
OUT="$(pwd)"
info "output: $OUT"

# -- 1. Compose files and container topology -------------------------------------------------

say "Recording what is running"

docker compose ls > compose-projects.txt 2>&1
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' > containers.txt 2>&1
docker volume ls > volumes.txt 2>&1
cat containers.txt | sed 's/^/    /'

# The compose file that defines the running stack is whatever `docker compose ls` reports, which
# is not necessarily the one in the repository. Copy every one it names.
mkdir -p compose-files
while read -r _name _status files; do
    [ -z "${files:-}" ] && continue
    IFS=',' read -ra paths <<< "$files"
    for path in "${paths[@]}"; do
        [ -f "$path" ] && cp "$path" "compose-files/$(echo "$path" | tr '/' '_')" 2>/dev/null
    done
done < <(tail -n +2 compose-projects.txt)
ls compose-files 2>/dev/null | sed 's/^/    saved: /' || warn "no compose files captured"

# Mounts and environment, per container. Env can hold a secret that exists nowhere else.
: > mounts.txt
: > env.txt
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
    { echo "=== $c ==="; docker inspect "$c" --format '{{json .Mounts}}'; } >> mounts.txt 2>&1
    { echo "=== $c ==="; docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}'; } >> env.txt 2>&1
done
chmod 600 env.txt
info "mounts.txt, env.txt (mode 600 -- env may contain secrets)"

# -- 2. The data that hides inside containers ------------------------------------------------

say "Rescuing container-internal data"

# Find the AURA backend container by image or name rather than assuming.
BACKEND="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE '^aura[-_]?(backend|gateway)$' | head -1)"
if [ -z "$BACKEND" ]; then
    warn "no aura backend container found; skipping the container-internal rescue"
else
    info "backend container: $BACKEND"
    APPDIR="$(docker inspect "$BACKEND" --format '{{.Config.WorkingDir}}' 2>/dev/null)"
    APPDIR="${APPDIR:-/app}"
    info "working dir: $APPDIR"

    if docker exec "$BACKEND" test -d "$APPDIR/local_qdrant_storage" 2>/dev/null; then
        if docker cp "$BACKEND:$APPDIR/local_qdrant_storage" ./local_qdrant_storage 2>/dev/null; then
            info "rescued local_qdrant_storage ($(du -sh local_qdrant_storage 2>/dev/null | cut -f1))"
        else
            warn "local_qdrant_storage exists but could not be copied -- copy it by hand before resizing"
        fi
    else
        info "no local_qdrant_storage (Qdrant Cloud in use, or nothing ingested) -- check QDRANT_HOST in env.txt"
    fi

    for f in learning_logs.json learning_dataset.jsonl telemetry.json; do
        docker cp "$BACKEND:$APPDIR/$f" "./$f" 2>/dev/null && info "rescued $f"
    done
fi

# -- 3. Databases ----------------------------------------------------------------------------

say "Dumping databases"

# Host-level Postgres, if there is one.
if command -v psql >/dev/null 2>&1 && sudo -u postgres psql -c '\l' > postgres-host-databases.txt 2>&1; then
    if sudo -u postgres pg_dumpall > postgres-host-all.sql 2>postgres-host-all.err; then
        info "host postgres: $(wc -c < postgres-host-all.sql) bytes"
    else
        warn "host postgres dump FAILED -- read postgres-host-all.err"
    fi
else
    info "no host-level postgres"
fi

# Containerised Postgres, whatever it is called.
for c in $(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i postgres | cut -f1); do
    if docker exec "$c" pg_dumpall -U postgres > "postgres-${c}-all.sql" 2>"postgres-${c}.err"; then
        info "$c: $(wc -c < "postgres-${c}-all.sql") bytes"
    else
        warn "$c dump FAILED -- read postgres-${c}.err (wrong superuser name?)"
    fi
done

# Redis. Mostly cache -- but the refresh-token store lives here too, so skipping it logs
# every user out on cutover.
for c in $(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i redis | cut -f1); do
    if docker exec "$c" redis-cli SAVE >/dev/null 2>&1 \
       && docker cp "$c:/data/dump.rdb" "./redis-${c}.rdb" 2>/dev/null; then
        info "$c: $(wc -c < "redis-${c}.rdb") bytes"
    else
        warn "$c snapshot failed (password-protected? try redis-cli -a)"
    fi
done

# -- 4. Edge configuration -------------------------------------------------------------------

say "Capturing edge configuration"
if command -v nginx >/dev/null 2>&1; then
    nginx -T > nginx-full-config.txt 2>&1 && info "nginx-full-config.txt"
    [ -d /etc/nginx/sites-enabled ] && cp -r /etc/nginx/sites-enabled ./nginx-sites-enabled 2>/dev/null
    [ -d /etc/letsencrypt ] && { tar czf letsencrypt.tgz -C /etc letsencrypt 2>/dev/null \
        && chmod 600 letsencrypt.tgz && info "letsencrypt.tgz (mode 600 -- contains private keys)"; }
else
    info "no host nginx"
fi

{ ss -tlnp 2>/dev/null; echo; ip -4 addr show scope global; } > listeners-and-addresses.txt 2>&1
{ nproc; free -h; df -h; lscpu; } > hardware.txt 2>&1

# -- 5. Verify, then say what is still needed -------------------------------------------------

say "Verifying"
find . -type f -size +0 | wc -l | xargs -I{} echo "    {} non-empty files"
du -sh . | cut -f1 | xargs -I{} echo "    total {}"

for required in containers.txt volumes.txt hardware.txt; do
    [ -s "$required" ] || warn "$required is empty -- that should not happen"
done
if ! ls postgres-*-all.sql >/dev/null 2>&1 && [ ! -s postgres-host-all.sql ]; then
    warn "NO database dump was produced. If this deployment has a database, stop and fix that."
fi

say "Done"
cat <<EOF
    Backup is in: $OUT

    IT IS NOT SAFE YET. It lives on the machine you are about to rebuild.
    Copy it off before you touch anything, from your workstation:

        scp -r root@<this-host>:$OUT ./aura-backup-\$(date +%F)

    Then verify the copy arrived intact -- check the byte counts above match.
EOF

[ "$WARNINGS" -gt 0 ] && { printf '\n\033[1;33m%d warning(s) above. Read them before proceeding.\033[0m\n' "$WARNINGS"; exit 2; }
exit 0
