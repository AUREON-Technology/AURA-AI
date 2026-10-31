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
#   AURA_HEALTH_URL=https://$AURA_DOMAIN/health/live   (a value ending in /health gets /live)
#   AURA_READY_TIMEOUT_S=300        how long to wait for /health/ready before failing the deploy
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

# -- 4b. ensure required secret files exist ------------------------------------------------
# A merge can add a new mandatory Docker secret to the compose file's `secrets:` list (e.g.
# secrets_encryption_key/github_oauth_client_secret, added alongside AURA_GITHUB_OAUTH_ENABLED)
# without this script knowing in advance -- Docker refuses to even create the container if the
# file a secret names is missing, and the failure looks like a broken migration:
#   invalid mount config for type "bind": bind source path does not exist: .../secrets/X
# bootstrap-secrets.sh is the script that creates these, but it is a one-time bring-up script
# nothing here used to call, so the *first* deploy after such a PR always failed this way and
# needed a human to SSH in and run it by hand. It only ever fills in a file that is missing (it
# prints "keeping existing X" and leaves a real secret alone otherwise), so calling it here on
# every routine deploy is safe.
if [ -f "$COMPOSE_DIR/scripts/bootstrap-secrets.sh" ]; then
  say "Ensuring required secret files exist"
  run "bash '$COMPOSE_DIR/scripts/bootstrap-secrets.sh'"
fi

# -- 4c. ensure settings this release expects exist in .env ---------------------------------
# The same gap as 4b, one level up. A merge can make a feature depend on a *setting* rather
# than a secret file, and deploy/.env exists only on the host (it is gitignored), so nothing in
# a pull request can set one: `git pull` ships the code for a feature that stays switched off
# until a human SSHes in. That is exactly how the engineer persona shipped unreachable --
# AURA_ENGINEER_AGENT_ROLES defaults empty, so AgentRegistry.resolve() fell back to the
# orchestrator for every caller and the Engineer silently answered as the Orchestrator.
#
# "Absent" and "present but empty" are the same thing to the code reading these -- an empty
# AURA_ENGINEER_AGENT_ROLES grants the capability to nobody, exactly like no line at all -- so
# both are treated as unconfigured here. A non-empty value is somebody's deliberate choice and
# is never rewritten; it is printed instead, because "already set" hid the one case that
# mattered: a key that is present, empty, and doing nothing.
ENV_FILE="$COMPOSE_DIR/.env"

# The current value of a key in $ENV_FILE: empty for absent, commented out, or set to nothing.
# Trailing inline comments and surrounding quotes are stripped so the value compares the way
# compose will read it.
env_value() {
  sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" 2>/dev/null \
    | head -1 | sed 's/[[:space:]]*#.*$//' | tr -d "\"'" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

set_env_value() {
  local key="$1" value="$2" note="$3"
  if [ "$DRY_RUN" = 1 ]; then
    info "[dry-run] would set ${key}=${value} in $ENV_FILE"
    return 0
  fi
  if grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
    # Present but empty: rewrite that one line in place rather than appending a duplicate,
    # which compose would resolve to whichever came last.
    sed -i "s|^[[:space:]]*${key}=.*|${key}=${value}|" "$ENV_FILE"
    info "$key: was empty, set to $value"
  else
    printf '\n# %s -- added by vps-update.sh\n%s=%s\n' "$note" "$key" "$value" >> "$ENV_FILE"
    info "$key: appended to $ENV_FILE (=$value)"
  fi
}

say "Ensuring deployment settings this release expects"
if [ ! -f "$ENV_FILE" ]; then
  info "no $ENV_FILE on this host; compose runs on the process environment alone -- skipping"
else
  ENGINEER_ROLES="$(env_value AURA_ENGINEER_AGENT_ROLES)"
  if [ -n "$ENGINEER_ROLES" ]; then
    info "AURA_ENGINEER_AGENT_ROLES=$ENGINEER_ROLES (already configured, left alone)"
  else
    set_env_value AURA_ENGINEER_AGENT_ROLES "Admin,SuperAdmin" \
      "roles granted the engineer_agent capability (aura_core/agents/registry.py)"
  fi

  # Per-user GitHub OAuth is what actually puts a repository in reach of that persona, but it
  # cannot be switched on from here alone: the App's client id, callback URL, and real client
  # secret exist only on the GitHub App's settings page. Enable it once both env halves are
  # present rather than turning on a connect flow that would fail at the authorize step, and
  # say exactly what is missing until then.
  OAUTH_ENABLED="$(env_value AURA_GITHUB_OAUTH_ENABLED)"
  if [ "$OAUTH_ENABLED" = "true" ]; then
    info "AURA_GITHUB_OAUTH_ENABLED=true (already on)"
  elif [ -n "$(env_value AURA_GITHUB_OAUTH_CLIENT_ID)" ] \
       && [ -n "$(env_value AURA_GITHUB_OAUTH_CALLBACK_URL)" ]; then
    set_env_value AURA_GITHUB_OAUTH_ENABLED "true" \
      "per-user GitHub OAuth for the engineer agent (api/v2/github_integration.py)"
  else
    info "AURA_GITHUB_OAUTH_ENABLED=${OAUTH_ENABLED:-<unset>}, left as is -- it needs, in $ENV_FILE:"
    info "  AURA_GITHUB_OAUTH_CLIENT_ID=<the GitHub App's Client ID>"
    info "  AURA_GITHUB_OAUTH_CALLBACK_URL=https://<domain>/api/v3/integrations/github/callback"
    info "  and the App's real client secret in $COMPOSE_DIR/secrets/github_oauth_client_secret"
    info "  (bootstrap-secrets.sh writes a random placeholder there, which will not authenticate)"
  fi
fi

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

# Release identity (BLOCK-03). Exported rather than written to .env: the SHA is a property of
# this deploy, not persistent host configuration, and compose interpolation reads the process
# environment ahead of --env-file, so these reach the build args in docker-compose.prod.yml
# without leaving a stale value behind for the next run to inherit.
#
# $AFTER is the commit the checkout was just fast-forwarded to, so the identity baked into the
# image is the source that actually built it -- not whatever origin/main moved to meanwhile.
export AURA_GIT_SHA="$AFTER"
export AURA_GIT_REF="${AURA_GIT_REF:-refs/heads/main}"
export AURA_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export AURA_DEPLOYMENT_ID="${AURA_DEPLOYMENT_ID:-vps-$(date -u +%Y%m%d%H%M%S)}"
info "release identity: ${AURA_GIT_SHA:0:12} built $AURA_BUILD_TIME (deploy $AURA_DEPLOYMENT_ID)"

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
# Nginx's official entrypoint only envsubst's /etc/nginx/templates → conf.d at
# *container start*. A bind-mounted template change (e.g. Phase H /api/v3/voice/live)
# is invisible to a long-lived nginx that `up -d` leaves "Running". Without recreate,
# the generic /api/ location keeps Connection "" and WebSocket upgrades 404.
if echo "$SERVICES" | grep -qx nginx; then
  info "force-recreating nginx so templates/*.template re-render"
  run "${DC[*]} up -d --force-recreate --no-deps nginx"
fi
run "${DC[*]} ps"

# -- 8. verify -----------------------------------------------------------------------------
# Three gates, all fatal (audit F-25):
#   8a  liveness through the edge  -- nginx is up and reaches a gateway process (HTTP 200 only)
#   8b  revision                   -- the process answering is the commit just built
#   8c  readiness                  -- /health/ready says healthy, schema at the image's head
# Liveness used to be `curl -f https://$domain/health`. nginx has no `/health` route and answers
# it with a 301 to `/health/`, which `curl -f` treats as success -- so that gate passed whenever
# nginx was up -- and readiness was printed but never failed a deploy.

say "Verifying"
if [ "$DRY_RUN" = 1 ]; then info "[dry-run] would poll the health endpoints"; exit 0; fi

if [ -n "${AURA_HEALTH_URL:-}" ]; then
  HEALTH_URL="$AURA_HEALTH_URL"
  # An override written for the old check (`.../health`) still works: point it at liveness.
  case "$HEALTH_URL" in
    */health) HEALTH_URL="${HEALTH_URL}/live" ;;
  esac
elif curl -fsS --max-time 2 http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
  # Only on a host whose override publishes the gateway on loopback (host-proxy.conf.example).
  HEALTH_URL="http://127.0.0.1:8000/health/live"
elif [ -f "$COMPOSE_DIR/.env" ]; then
  DOMAIN="$(sed -n 's/^AURA_DOMAIN=//p' "$COMPOSE_DIR/.env" | head -1 | tr -d "[:space:]\"'")"
  if [ -n "$DOMAIN" ]; then
    HEALTH_URL="https://${DOMAIN}/health/live"
  else
    HEALTH_URL="http://127.0.0.1:8000/health/live"
  fi
else
  HEALTH_URL="http://127.0.0.1:8000/health/live"
fi
info "health url: $HEALTH_URL"
# The edge's base URL, for the WebSocket probe below.
EDGE_BASE="${HEALTH_URL%/health/live}"

# 8a. Exactly 200. Not `curl -f`: that passes any 3xx, and a redirect here means the URL is
# wrong (http:// instead of https://, or a path nginx does not route), not that the app is up.
ok=0
LIVE_CODE="000"
for attempt in $(seq 1 30); do
  LIVE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null || true)"
  if [ "$LIVE_CODE" = 200 ]; then ok=1; break; fi
  printf '    waiting for %s (%d/30, last HTTP %s)\r' "$HEALTH_URL" "$attempt" "${LIVE_CODE:-000}"
  sleep 4
done
echo
[ "$ok" = 1 ] || {
  printf '\n\033[1;31mFAILED: %s never answered 200 (last HTTP %s)\033[0m\n' "$HEALTH_URL" "${LIVE_CODE:-000}" >&2
  case "$LIVE_CODE" in
    3??) echo "    A redirect: the URL is wrong for this edge (https://, and /health/live exactly)." >&2 ;;
  esac
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

# -- 8b. the running build is the build we just shipped (BLOCK-03) -------------------------
# The check this deploy path did not have. Production ran a 2026-09-13 image for two weeks while
# every later merge reported nothing wrong, because "the stack restarted" was the only thing ever
# verified -- never "the restarted stack is the new code".
#
# Asked of the gateway container directly, not through nginx: the claim under test is "the new
# image is running", and routing it through the edge would let an unrelated DNS or TLS fault
# (BLOCK-01) fail a deploy that actually succeeded -- or, worse, let a stale-but-reachable edge
# answer for it. The edge is checked separately below, which is the right place for that.
#
# `python`, not curl: the runner stage purges curl after fetching piper.
say "Verifying the deployed revision"
REVISION_PROBE='import urllib.request,sys
try:
    sys.stdout.write(urllib.request.urlopen("http://127.0.0.1:8000/health/version", timeout=10).read().decode())
except Exception as exc:
    sys.stderr.write(str(exc)); sys.exit(1)'

LIVE_JSON=""
for _rev_try in $(seq 1 15); do
  LIVE_JSON="$("${DC[@]}" exec -T "$BACKEND_SERVICE" python -c "$REVISION_PROBE" 2>/dev/null || true)"
  [ -n "$LIVE_JSON" ] && break
  printf '    waiting for /health/version (%d/15)\r' "$_rev_try"
  sleep 4
done
echo

LIVE_SHA="$(printf '%s' "$LIVE_JSON" \
  | sed -n 's/.*"git_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"

if [ -z "$LIVE_JSON" ]; then
  printf '\n\033[1;31mFAILED: the gateway never answered /health/version\033[0m\n' >&2
  cat >&2 <<VERFAIL

    This endpoint ships in the same commit as this script, so no answer from the container means
    it is still running an older image -- exactly the silent-stale-deploy condition this check
    exists to catch. The stack restarted, but not onto $AFTER.

    What is actually running:
      ${DC[*]} images $BACKEND_SERVICE
      ${DC[*]} logs --tail=60 $BACKEND_SERVICE

    If the process is refusing to boot, check for an unidentified build: AuraSettings.validate()
    rejects a production start with no AURA_GIT_SHA baked in, which means the build args did not
    reach docker compose build.
VERFAIL
  exit 1
fi

if [ "$LIVE_SHA" != "$AFTER" ]; then
  printf '\n\033[1;31mFAILED: deployed revision does not match\033[0m\n' >&2
  cat >&2 <<VERMISMATCH

    built and expected:  $AFTER
    actually serving:    ${LIVE_SHA:-<none reported>}

    The restart did not put $AFTER into service. Most often a build that succeeded against a
    cached layer while the running container was never recreated.

    Force it:
      ${DC[*]} up -d --force-recreate --no-deps $BACKEND_SERVICE
VERMISMATCH
  exit 1
fi
info "revision:  OK  (serving ${LIVE_SHA:0:12}, matches the commit just built)"

# -- 8c. readiness gates the deploy (audit F-25) --------------------------------------------
# Asked of the gateway container directly, like the revision probe and for the same reason: the
# claim is about the gateway's dependencies, and the edge was verified separately in 8a.
#
# Fatal, after a bounded window. Ready means: HTTP 200, `healthy: true`, and -- when this deploy
# just ran migrations against a Postgres in this stack -- a `schema` block that is healthy *and*
# names the revision it verified. The schema check deliberately reports healthy-but-unverified
# when it cannot reach Postgres (so a DB blip does not evict a serving gateway), which is right
# for traffic and wrong for a deploy gate: right after a migration that is exactly the case to
# refuse. Nothing is rolled back automatically -- the new code is serving and the edge is up
# (nginx no longer waits for readiness); the deploy is marked failed so a human looks.
#
# Readiness includes "at least one LLM provider is healthy", so an inference outage fails the
# deploy too. That is intended: a release that cannot answer a chat turn is not a success.
READY_TIMEOUT_S="${AURA_READY_TIMEOUT_S:-300}"
EXPECT_SCHEMA=0
[ "$RUN_MIGRATIONS" = 1 ] && [ -n "$BACKEND_DIR" ] && [ "$HAS_DB" = 1 ] && EXPECT_SCHEMA=1

READY_PROBE='import json, os, sys, urllib.error, urllib.request
try:
    resp = urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=15)
    code, body = resp.status, resp.read().decode()
except urllib.error.HTTPError as exc:
    code, body = exc.code, exc.read().decode()
except Exception as exc:
    print("NOT_READY: /health/ready unreachable: %s: %s" % (type(exc).__name__, exc))
    sys.exit(1)
try:
    payload = json.loads(body)
except ValueError:
    print("NOT_READY: HTTP %s, body is not JSON" % code)
    print(body[:2000])
    sys.exit(1)
problems = []
if code != 200:
    problems.append("HTTP %s" % code)
if payload.get("healthy") is not True:
    problems.append("healthy is not true")
schema = (payload.get("checks") or {}).get("schema")
if schema is None:
    if os.environ.get("AURA_EXPECT_SCHEMA") == "1":
        problems.append("no schema block, though migrations just ran against Postgres")
elif schema.get("healthy") is not True:
    problems.append("schema: %s (at %s, image expects %s)" % (
        schema.get("detail", "unhealthy"), schema.get("revision"), schema.get("expected")))
elif not schema.get("revision"):
    problems.append("schema unverified: %s" % schema.get("detail", "no revision reported"))
print("NOT_READY: " + "; ".join(problems) if problems else "READY")
print(json.dumps(payload, indent=2, sort_keys=True)[:4000])
sys.exit(1 if problems else 0)'

say "Waiting for readiness (up to ${READY_TIMEOUT_S}s)"
READY_OUT=""
ready=0
_ready_deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
while :; do
  if READY_OUT="$("${DC[@]}" exec -T -e "AURA_EXPECT_SCHEMA=$EXPECT_SCHEMA" "$BACKEND_SERVICE" \
                    python -c "$READY_PROBE" 2>&1)"; then
    ready=1; break
  fi
  [ "$(date +%s)" -ge "$_ready_deadline" ] && break
  printf '    %s\r' "$(printf '%s' "$READY_OUT" | head -1 | cut -c1-110)"
  sleep 10
done
echo
if [ "$ready" != 1 ]; then
  printf '\n\033[1;31mFAILED: the gateway is not ready after %ss\033[0m\n' "$READY_TIMEOUT_S" >&2
  printf '%s\n' "$READY_OUT" | head -80 | sed 's/^/      /' >&2
  cat >&2 <<NOTREADY

    ${AFTER:0:8} is running and the edge is serving (liveness passed), but /health/ready does
    not report it fit to serve. Read the verdict line above, then:
      providers all unhealthy   inference is down: ${DC[*]} logs --tail=60 ollama
      schema behind/unverified  ${DC[*]} run --rm --no-deps -T $BACKEND_SERVICE alembic upgrade head
    Gateway log: ${DC[*]} logs --tail=60 $BACKEND_SERVICE
    Roll back:   git -C $REPO checkout ${BEFORE} && ${DC[*]} up -d --build $BACKEND_SERVICE
    A slow cold start can be given longer with AURA_READY_TIMEOUT_S (now ${READY_TIMEOUT_S}).
NOTREADY
  exit 1
fi
info "readiness: OK  ($(printf '%s' "$READY_OUT" | head -1))"

# Phase H: confirm the edge still upgrades /api/v3/voice/live. A 404 here most likely means
# nginx negotiated HTTP/2 with this curl (ALPN offers h2 by default) -- HTTP/2 forbids the
# legacy Connection/Upgrade headers the WebSocket handshake needs, so the request silently
# degrades to a plain GET against a websocket-only route. `http2 on;` was removed from both
# HTTPS server blocks in aura.conf.template for exactly this reason; if this still 404s,
# check for `http2 on;` having crept back in before suspecting the location block itself.
# Retried: force-recreate leaves nginx briefly unready and a single probe can false-alarm.
WS_URL="${EDGE_BASE}/api/v3/voice/live"
WS_CODE="000"
for _ws_try in $(seq 1 15); do
  # `|| true`, not `|| echo 000`: curl already writes 000 on a connection failure, and the
  # echo appended a second one.
  WS_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    "$WS_URL" 2>/dev/null || true)"
  case "$WS_CODE" in
    101|4??) break ;;  # 101 success; 4xx is a real edge/app answer (not startup flake)
  esac
  sleep 2
done
case "$WS_CODE" in
  101) info "voice live WS: OK  (101 Switching Protocols)" ;;
  404)
    info "voice live WS: HTTP $WS_CODE at $WS_URL -- check for \`http2 on;\` in aura.conf.template
       (ALPN negotiating h2 breaks the legacy Upgrade handshake this route needs)"
    ;;
  *)
    # Auth/feature-off closes after accept often surface as non-101; not fatal for deploy.
    info "voice live WS: HTTP $WS_CODE at $WS_URL (expect 101 when live is enabled)"
    ;;
esac

say "Deployed ${AFTER:0:8}"
info "previous commit was ${BEFORE:0:8} -- keep it, that is your rollback target"
