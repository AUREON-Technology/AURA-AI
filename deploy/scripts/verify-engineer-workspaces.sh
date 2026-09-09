#!/usr/bin/env bash
# Phase J live verification -- persistent engineer-agent workspaces.
#
# Run this ON THE VPS, from the checkout at /opt/aura-stack, once the sandbox is enabled:
#
#     # deploy/.env needs BOTH of these; AURA_SANDBOX_ENABLED alone only flips the gateway flag
#     # and leaves the socket-proxy and egress-proxy services down.
#     AURA_SANDBOX_ENABLED=true
#     COMPOSE_PROFILES=sandbox
#
#     bash deploy/scripts/verify-engineer-workspaces.sh
#
# Why this exists: the default `pytest` run uses an in-memory runner that never touches the
# Docker API, and no CI job stands up docker-socket-proxy. Every claim about the deployed
# sandbox -- bind-mount path identity, unprivileged read-only containers, allowlisted egress,
# the reaper -- is therefore unproven until it is exercised against a real daemon, which is what
# this does. Nine checks are scripted; the tenth needs a real agent session and is printed as a
# checklist rather than claimed.
#
# Safe to run on a live host: it works only in its own workspace (user "phase-j-verifier") and
# destroys it on the way out, including when a check fails.
#
# Exit status is the number of failed checks; 0 means every scripted check passed.
set -uo pipefail

COMPOSE_FILE=${AURA_COMPOSE_FILE:-deploy/docker-compose.prod.yml}
SERVICE=${AURA_GATEWAY_SERVICE:-gateway}
DRIVER="$(dirname "$0")/verify_engineer_workspaces.py"
REPORT_DIR=${AURA_VERIFY_REPORT_DIR:-docs/reports}
REPORT="$REPORT_DIR/phase-j-workspaces-$(date -u +%Y%m%dT%H%M%SZ).txt"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\033[1;31m    %s\033[0m\n' "$*" >&2; exit 125; }

running_services() {
    docker compose -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null
}

say "Preflight"

command -v docker >/dev/null 2>&1 || die "docker is not on PATH; run this on the VPS host."
[ -f "$COMPOSE_FILE" ] || die "no $COMPOSE_FILE here. Run from the repository root (/opt/aura-stack)."
[ -f "$DRIVER" ] || die "missing $DRIVER"

running_services | grep -qx "$SERVICE" || die "the '$SERVICE' service is not running. Bring the stack up first."
info "gateway is up"

# The sandbox services live behind `profiles: [sandbox]`. Without COMPOSE_PROFILES they simply
# never start, and every check below would fail for that one reason -- which is worth saying
# plainly here rather than letting it look like the feature itself is broken.
for required in docker-socket-proxy sandbox-proxy; do
    running_services | grep -qx "$required" || die "'$required' is not running. Set
    COMPOSE_PROFILES=sandbox in deploy/.env (as well as AURA_SANDBOX_ENABLED=true) and bring the
    stack up again; the sandbox services are behind a compose profile and never start without it."
    info "$required is up"
done

enabled=$(docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
    sh -lc 'printf %s "${AURA_SANDBOX_ENABLED:-unset}"' 2>/dev/null | tr -d '[:space:]')
case "$enabled" in
    true|True|TRUE|1) info "AURA_SANDBOX_ENABLED=$enabled inside the gateway" ;;
    *) die "the gateway has AURA_SANDBOX_ENABLED=$enabled. The runner is not built, so nothing
    below can run. Set it in deploy/.env and recreate the gateway." ;;
esac

say "Running checks inside the gateway"

GATEWAY_CID=$(docker compose -f "$COMPOSE_FILE" ps -q "$SERVICE")
[ -n "$GATEWAY_CID" ] || die "could not resolve the gateway container id"
docker cp "$DRIVER" "$GATEWAY_CID":/tmp/verify_phase_j.py || die "could not copy the driver into the gateway"

mkdir -p "$REPORT_DIR"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python /tmp/verify_phase_j.py 2>&1 | tee "$REPORT"
status=${PIPESTATUS[0]}

# Strip the ANSI colouring so the saved report is quotable in the roadmap.
sed -i 's/\x1b\[[0-9;]*m//g' "$REPORT" 2>/dev/null || true
docker exec "$GATEWAY_CID" rm -f /tmp/verify_phase_j.py >/dev/null 2>&1 || true

say "Result"
info "report written to $REPORT"
if [ "$status" -eq 0 ]; then
    info "Every scripted check passed. Check 10 (exactly one confirmation prompt, at git_push)"
    info "is manual -- complete it before marking Phase J proven in docs/AURA_UPGRADE_ROADMAP.md."
else
    info "$status check(s) failed. Phase J stays 'built, not proven' until they pass."
fi
exit "$status"
