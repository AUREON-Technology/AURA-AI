#!/usr/bin/env bash
#
# Update a running AURA deployment on a VPS to the current `main`.
#
# This is an *update* script, not a provisioning script. It assumes a stack is already running
# and does the four things a code update needs -- pull, migrate, rebuild, verify -- while
# refusing to guess about anything it cannot determine. Provisioning a fresh host from
# deploy/docker-compose.prod.yml is a different job; see docs/guides/deployment-guide.md.
#
# It is written to be safe to run on a host whose layout has drifted from the repository,
# because that is the situation it was written for: /opt/aura on the reference host is not a
# git checkout, the checkout is nested one level down, and the running stack is a
# hand-assembled compose file rather than the one in deploy/.
#
# Design rules, all of which exist because the alternative bites during an outage:
#
#   * Nothing is inferred silently. Every discovered path is printed before it is used.
#   * `git merge --ff-only`, never `reset --hard`. Untracked files are ignored (logs, tmp).
#     Tracked local edits: interactive runs stop; CI sets AURA_STASH_DIRTY=1 and stashes
#     them with a timestamped message so a leftover hotfix cannot block every auto-deploy.
#   * The pre-update commit is recorded and the rollback command is printed on failure. It is
#     not run automatically -- an automatic rollback on a health-check flake is its own outage.
#   * Migrations run before the new code starts, and only forward. `0002_client_updated_at`
#     adds nullable columns with no backfill, so it is safe to apply to a live database, but
#     that is a property of this migration and not a promise about the next one.
#   * A failed health check is a non-zero exit. A deploy script that exits 0 on a broken
#     deployment is worse than no script.
#
# Usage:
#   ./vps-update.sh                 # discover everything, update, verify
#   ./vps-update.sh --dry-run       # print what it would do, touch nothing
#   ./vps-update.sh --no-migrate    # skip alembic (use when the DB is managed elsewhere)
#
# Environment overrides, for hosts this script cannot work out on its own:
#   AURA_REPO=/path/to/checkout     AURA_COMPOSE=/path/to/compose.yml
#   AURA_PROJECT=aura               AURA_BACKEND_SERVICE=backend
#   AURA_COMPOSE_OVERRIDE=/path/to/override.yml[:/more.yml]
#                                   extra -f files merged over AURA_COMPOSE. Defaults to a
#                                   docker-compose.override.yml sitting next to it.
#   AURA_HEALTH_URL=https://$AURA_DOMAIN/health
#   AURA_SHA=<commit>               pin to the SHA CI tested (must be on origin/main)
#   AURA_STASH_DIRTY=1              stash tracked local edits instead of aborting (CI default)

set -euo pipefail

DRY_RUN=0
RUN_MIGRATIONS=1
for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=1 ;;
    --no-migrate) RUN_MIGRATIONS=0 ;;
    # Print the whole header block rather than a hardcoded line range, which silently
    # truncated --help every time the header grew.
    -h|--help)    awk 'NR>1 && !/^#/{exit} NR>1' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }
run()  {
  if [ "$DRY_RUN" = 1 ]; then printf '    [dry-run] %s\n' "$*"; else eval "$@"; fi
}

# -- 1. locate the checkout ---------------------------------------------------------------
# The repository root is wherever `.git` is, which on a drifted host is not where you would
# guess. Candidates are tried in order and the first real checkout wins.

say "Locating the checkout"
REPO="${AURA_REPO:-}"
if [ -z "$REPO" ]; then
  for candidate in /opt/aura /opt/aura/aura_backend /opt/aura/AURA-AI /srv/aura "$PWD"; do
    if [ -d "$candidate/.git" ]; then REPO="$candidate"; break; fi
  done
fi
[ -n "$REPO" ] || die "no git checkout found. Set AURA_REPO=/path/to/checkout and re-run.
       Find it with:  find /opt /srv /root /home -maxdepth 4 -name .git -type d 2>/dev/null"
REPO="$(cd "$REPO" && pwd)"
info "checkout: $REPO"

git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "$REPO is not a usable git checkout"

# The backend package can sit at the root or one level down, depending on how this host was
# built. Alembic must run from whichever directory holds alembic.ini.
if   [ -f "$REPO/aura_backend/alembic.ini" ]; then BACKEND_DIR="$REPO/aura_backend"
elif [ -f "$REPO/alembic.ini" ];              then BACKEND_DIR="$REPO"
else BACKEND_DIR=""; fi
[ -n "$BACKEND_DIR" ] && info "backend dir: $BACKEND_DIR" || info "backend dir: not found (migrations will be skipped)"

# -- 2. record the rollback point ---------------------------------------------------------

BEFORE="$(git -C "$REPO" rev-parse HEAD)"
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
info "currently on $BRANCH at ${BEFORE:0:8}"

TRACKED_DIRTY="$(git -C "$REPO" status --porcelain --untracked-files=no)"
if [ -n "$TRACKED_DIRTY" ]; then
  say "Uncommitted tracked changes present"
  git -C "$REPO" status --short --untracked-files=no | sed 's/^/    /'
  if [ "${AURA_STASH_DIRTY:-0}" = "1" ]; then
    STASH_MSG="cd-pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)"
    info "AURA_STASH_DIRTY=1 -- stashing as '$STASH_MSG' so fast-forward can proceed"
    info "recover with: git -C $REPO stash list"
    run "git -C '$REPO' stash push -m '$STASH_MSG'"
    if [ "$DRY_RUN" != "1" ] && [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
      die "stash did not clear tracked modifications; refusing to continue"
    fi
  else
    die "this host has local modifications. Committing or stashing them is a decision for you,
       not for a deploy script. Review the list above, then either:
         git -C $REPO stash push -m pre-deploy
       or re-run with AURA_STASH_DIRTY=1 (what Deploy VPS does)."
  fi
fi

# -- 3. pull ------------------------------------------------------------------------------

say "Fetching origin/main"
git -C "$REPO" remote -v | sed 's/^/    /'
info "note: if this fails with 'Password authentication is not supported', the remote is HTTPS"
info "      without a credential. Fix with a token or a deploy key -- see the notes at the end."

run "git -C '$REPO' fetch origin main"

if [ "$BRANCH" != "main" ]; then
  info "on '$BRANCH', not main -- checking out main"
  run "git -C '$REPO' checkout main"
fi

TARGET="origin/main"
if [ -n "${AURA_SHA:-}" ]; then
  git -C "$REPO" rev-parse --verify "${AURA_SHA}^{commit}" >/dev/null 2>&1 \
    || die "AURA_SHA=${AURA_SHA} is not a commit this checkout knows after fetch"
  git -C "$REPO" merge-base --is-ancestor "$AURA_SHA" origin/main \
    || die "AURA_SHA=${AURA_SHA:0:8} is not on origin/main. Refusing to deploy a commit that is not on the protected branch."
  HEAD_NOW="$(git -C "$REPO" rev-parse HEAD)"
  PIN="$(git -C "$REPO" rev-parse "$AURA_SHA")"
  if [ "$HEAD_NOW" != "$PIN" ] && git -C "$REPO" merge-base --is-ancestor "$AURA_SHA" HEAD; then
    info "HEAD already contains ${AURA_SHA:0:8} -- a newer deploy won the race, nothing to do"
    exit 0
  fi
  TARGET="$AURA_SHA"
  info "pinning to AURA_SHA=${AURA_SHA:0:8}"
fi

# --ff-only so a diverged host stops here rather than creating a surprise merge commit.
run "git -C '$REPO' merge --ff-only $TARGET" \
  || die "cannot fast-forward to $TARGET: this checkout has diverged from origin/main.
       Inspect with:  git -C $REPO log --oneline --graph HEAD...origin/main"

AFTER="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "$BEFORE")"
if [ "$BEFORE" = "$AFTER" ] && [ "$DRY_RUN" = 0 ]; then
  info "already up to date at ${AFTER:0:8} -- rebuilding anyway, since the image may be older"
else
  info "now at ${AFTER:0:8}"
  git -C "$REPO" log --oneline "${BEFORE}..${AFTER}" 2>/dev/null | sed 's/^/    /' || true
fi

# -- 4. locate the compose file and project ----------------------------------------------
# The running stack is the source of truth, not the repository: this host may be running a
# hand-written compose file that predates deploy/docker-compose.prod.yml.

say "Locating the running stack"
docker compose version >/dev/null 2>&1 || die "docker compose v2 not available on this host"

COMPOSE="${AURA_COMPOSE:-}"
PROJECT="${AURA_PROJECT:-}"
# Extra files passed after $COMPOSE, in order. Compose merges later -f files over earlier
# ones, so these are overrides.
COMPOSE_EXTRA=()

if [ -z "$COMPOSE" ]; then
  # `docker compose ls` reports the config file(s) of every running project, which is the only
  # reliable way to find the file that actually started what is running.
  while IFS= read -r line; do
    name="$(echo "$line" | awk '{print $1}')"
    files="$(echo "$line" | awk '{print $NF}')"
    case "$name" in
      aura*|AURA*)
        PROJECT="${PROJECT:-$name}"
        if [ -z "$COMPOSE" ]; then
          # A stack started with several -f files is reported here as a comma-separated
          # list. Keep all of them. Taking only the first silently restarts the stack
          # without its overrides, which is how a host that deliberately runs no bundled
          # nginx (see deploy/nginx/host-proxy.conf.example) gets one back, fighting the
          # host nginx for 80/443.
          COMPOSE="${files%%,*}"
          rest="${files#*,}"
          if [ "$rest" != "$files" ]; then
            IFS=',' read -r -a _reported <<< "$rest"
            for _f in "${_reported[@]}"; do
              [ -n "$_f" ] && COMPOSE_EXTRA+=("$_f")
            done
          fi
        fi
        ;;
    esac
  done < <(docker compose ls --format table 2>/dev/null | tail -n +2)
fi

if [ -z "$COMPOSE" ]; then
  for candidate in /opt/aura/docker-compose.vps.yml "$REPO/docker-compose.vps.yml" \
                   "$REPO/deploy/docker-compose.prod.yml" "$REPO/docker-compose.yml"; do
    [ -f "$candidate" ] && { COMPOSE="$candidate"; break; }
  done
fi
[ -n "$COMPOSE" ] || die "could not find the compose file. Set AURA_COMPOSE=/path/to/file.yml
       List running stacks with:  docker compose ls"

PROJECT="${PROJECT:-aura}"
COMPOSE_DIR="$(cd "$(dirname "$COMPOSE")" && pwd)"
info "compose file: $COMPOSE"
info "project:      $PROJECT"

# Compose auto-loads docker-compose.override.yml only when no -f is given. This script always
# passes -f, so without adding it explicitly the override is ignored -- including the one
# deploy/nginx/host-proxy.conf.example instructs operators to write on a shared host, which is
# what binds the gateway to a loopback port instead of publishing 80/443.
if [ ${#COMPOSE_EXTRA[@]} -eq 0 ]; then
  for _candidate in "$COMPOSE_DIR/docker-compose.override.yml" \
                    "$COMPOSE_DIR/docker-compose.override.yaml"; do
    [ -f "$_candidate" ] && { COMPOSE_EXTRA+=("$_candidate"); break; }
  done
fi
# An explicit list always wins, so a host with an override kept elsewhere can name it.
if [ -n "${AURA_COMPOSE_OVERRIDE:-}" ]; then
  COMPOSE_EXTRA=()
  IFS=':' read -r -a _explicit <<< "$AURA_COMPOSE_OVERRIDE"
  for _f in "${_explicit[@]}"; do
    [ -n "$_f" ] || continue
    [ -f "$_f" ] || die "AURA_COMPOSE_OVERRIDE names a file that does not exist: $_f"
    COMPOSE_EXTRA+=("$_f")
  done
fi

DC=(docker compose -p "$PROJECT" -f "$COMPOSE")
for _f in ${COMPOSE_EXTRA[@]+"${COMPOSE_EXTRA[@]}"}; do
  DC+=(-f "$_f")
  info "override:     $_f"
done
if [ -f "$COMPOSE_DIR/.env" ]; then
  DC+=(--env-file "$COMPOSE_DIR/.env")
  info "env file:     $COMPOSE_DIR/.env"
fi
"${DC[@]}" config --services 2>/dev/null | sed 's/^/    service: /' \
  || die "docker compose cannot parse $COMPOSE"

SERVICES="$("${DC[@]}" config --services 2>/dev/null || true)"

# Pick the backend service by name, preferring an explicit override.
BACKEND_SERVICE="${AURA_BACKEND_SERVICE:-}"
if [ -z "$BACKEND_SERVICE" ]; then
  for name in gateway backend aura-backend api web app; do
    if echo "$SERVICES" | grep -qx "$name"; then BACKEND_SERVICE="$name"; break; fi
  done
fi
[ -n "$BACKEND_SERVICE" ] || die "could not identify the backend service among:
$(echo "$SERVICES" | sed 's/^/         /')
       Set AURA_BACKEND_SERVICE=<name> and re-run."
info "backend service: $BACKEND_SERVICE"

HAS_DB=0
echo "$SERVICES" | grep -qE '^(postgres|db|database)$' && HAS_DB=1
info "postgres in this stack: $([ "$HAS_DB" = 1 ] && echo yes || echo 'no (managed elsewhere, or not used)')"

# -- 5. build ------------------------------------------------------------------------------
# Built before anything is stopped, so a build failure costs no downtime at all.

say "Building application images"
# `compose build` only builds services with a `build:` key (gateway, workers, beat,
# backup). Pulled images stay as they are. Building all of them here is what keeps
# Celery on the same commit as the gateway -- `up -d` will not rebuild an image
# that was never tagged.
run "${DC[*]} build --pull" || die "build failed -- nothing was restarted, the
       running stack is untouched. Fix the build and re-run."

# -- 6. migrate ----------------------------------------------------------------------------
# Forward-only, before the new code starts. The two migrations in this repository are additive
# (0002 adds nullable columns, no backfill), so old code tolerates the new schema -- which is
# what makes migrate-then-restart safe rather than requiring a maintenance window.

if [ "$RUN_MIGRATIONS" = 1 ] && [ -n "$BACKEND_DIR" ]; then
  say "Applying database migrations"
  if [ "$HAS_DB" = 1 ]; then
    run "${DC[*]} up -d postgres 2>/dev/null || ${DC[*]} up -d db 2>/dev/null || true"
  fi
  # Run inside the freshly built image so alembic uses the new revisions and the container's
  # own DATABASE_URL, rather than whatever happens to be in the host shell.
  if ! run "${DC[*]} run --rm --no-deps -T $BACKEND_SERVICE alembic upgrade head"; then
    die "migration failed. The old stack is still running and still serving.
       Read the error above. Nothing was restarted, so there is nothing to roll back."
  fi
else
  say "Skipping migrations"
  info "$([ "$RUN_MIGRATIONS" = 0 ] && echo 'requested with --no-migrate' || echo 'no alembic.ini found')"
fi

# -- 7. restart ----------------------------------------------------------------------------

say "Restarting the stack"
run "${DC[*]} up -d --remove-orphans"
run "${DC[*]} ps"

# -- 8. verify -----------------------------------------------------------------------------
# Liveness only says the process is up; readiness is the one that checks dependencies, so it
# is the one worth gating on. Retried, because a cold start pulls a model into memory.

say "Verifying"
if [ "$DRY_RUN" = 1 ]; then info "[dry-run] would poll the health endpoints"; exit 0; fi

if [ -n "${AURA_HEALTH_URL:-}" ]; then
  HEALTH_URL="$AURA_HEALTH_URL"
elif curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  HEALTH_URL="http://127.0.0.1:8000/health"
elif [ -f "$COMPOSE_DIR/.env" ]; then
  DOMAIN="$(sed -n 's/^AURA_DOMAIN=//p' "$COMPOSE_DIR/.env" | head -1 | tr -d "[:space:]\"'")"
  if [ -n "$DOMAIN" ]; then
    HEALTH_URL="https://${DOMAIN}/health"
  else
    HEALTH_URL="http://127.0.0.1:8000/health"
  fi
else
  HEALTH_URL="http://127.0.0.1:8000/health"
fi
info "health url: $HEALTH_URL"
READY_URL="${HEALTH_URL%/health}/health/ready"

ok=0
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then ok=1; break; fi
  printf '    waiting for %s (%d/30)\r' "$HEALTH_URL" "$attempt"
  sleep 4
done
echo
[ "$ok" = 1 ] || {
  printf '\n\033[1;31mFAILED: %s never became healthy\033[0m\n' "$HEALTH_URL" >&2
  echo "    Last 60 lines from $BACKEND_SERVICE:" >&2
  "${DC[@]}" logs --tail=60 "$BACKEND_SERVICE" >&2 || true
  cat >&2 <<ROLLBACK

    To roll back to the previous commit and rebuild:
      git -C $REPO checkout ${BEFORE}
      ${DC[*]} up -d --build $BACKEND_SERVICE

    The migrations applied above are additive and the old code ignores the new columns, so
    rolling the code back does not require rolling the schema back.
ROLLBACK
  exit 1
}

info "liveness:  OK  ($HEALTH_URL)"
if curl -fsS --max-time 10 "$READY_URL" >/dev/null 2>&1; then
  info "readiness: OK  ($READY_URL)"
else
  # Not fatal: /health/ready only exists on the v3 gateway, and a v1/v2-only host has no such
  # endpoint. A dependency that is genuinely down shows up in the body, so it is printed.
  info "readiness: not OK or not present at $READY_URL"
  curl -sS --max-time 10 "$READY_URL" 2>&1 | head -20 | sed 's/^/      /' || true
fi

say "Deployed ${AFTER:0:8}"
info "previous commit was ${BEFORE:0:8} -- keep it, that is your rollback target"
