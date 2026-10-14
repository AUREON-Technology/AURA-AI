#!/usr/bin/env bash
# ADR-030 Phase 6 live verification -- hosted open-weight inference.
#
# Run this ON THE VPS, from the checkout at /opt/aura-stack, once a hosted slot is enabled:
#
#     printf '%s' "$KEY" > deploy/secrets/hosted1_api_key
#     # deploy/.env (see the "Hosted open-weight providers" block in deploy/.env.example):
#     AURA_ALLOW_HOSTED_INFERENCE=true
#     AURA_LLM_PROVIDERS=hosted1,local
#     AURA_LLM_ROUTING_POLICY=quality_first
#     AURA_PROVIDER_HOSTED1_BASE_URL=...   AURA_PROVIDER_HOSTED1_MODEL=...   (+ limits)
#     # recreate the gateway, then:
#     bash deploy/scripts/verify-hosted-inference.sh
#
# Why this exists: no test in the default suite makes a network call, the provider contract
# tests replay *documented* response shapes rather than live ones, and quota behaviour depends
# on limits only the real provider enforces. Ten checks are scripted (health, real completions
# and streams, SENSITIVE content staying local, kernel turns, the audit log, persisted quota
# counters, a declared budget, and a real 429 driven on purpose); the eleventh needs the app and
# is printed as a checklist rather than claimed.
#
# It spends real quota -- a few small completions plus a short burst of ~2,000-token prompts to
# cross the per-minute token limit. Set AURA_VERIFY_SKIP_429=1 to leave the burst out (check 10
# then reports SKIP, and the 429 path stays unproven).
#
# Exit status is the number of failed checks; 0 means every scripted check passed.
set -uo pipefail

COMPOSE_FILE=${AURA_COMPOSE_FILE:-deploy/docker-compose.prod.yml}
SERVICE=${AURA_GATEWAY_SERVICE:-gateway}
DRIVER="$(dirname "$0")/verify_hosted_inference.py"
REPORT_DIR=${AURA_VERIFY_REPORT_DIR:-docs/reports}
REPORT="$REPORT_DIR/phase-6-hosted-inference-$(date -u +%Y%m%dT%H%M%SZ).txt"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\033[1;31m    %s\033[0m\n' "$*" >&2; exit 125; }

gateway_env() {
    docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
        sh -lc "printf %s \"\${$1:-}\"" 2>/dev/null | tr -d '[:space:]'
}

say "Preflight"

command -v docker >/dev/null 2>&1 || die "docker is not on PATH; run this on the VPS host."
[ -f "$COMPOSE_FILE" ] || die "no $COMPOSE_FILE here. Run from the repository root (/opt/aura-stack)."
[ -f "$DRIVER" ] || die "missing $DRIVER"

docker compose -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null | grep -qx "$SERVICE" \
    || die "the '$SERVICE' service is not running. Bring the stack up first."
info "gateway is up"

allow=$(gateway_env AURA_ALLOW_HOSTED_INFERENCE)
case "$allow" in
    true|True|TRUE|1|yes|on) info "AURA_ALLOW_HOSTED_INFERENCE=$allow inside the gateway" ;;
    *) die "the gateway has AURA_ALLOW_HOSTED_INFERENCE='$allow'. Set it to true in deploy/.env and
    recreate the gateway (docker compose up -d gateway); nothing below can run without it." ;;
esac

providers=$(gateway_env AURA_LLM_PROVIDERS)
case ",$providers," in
    *,hosted1,*|*,hosted2,*|*,hosted3,*) info "AURA_LLM_PROVIDERS=$providers" ;;
    *) die "AURA_LLM_PROVIDERS='$providers' lists no hosted slot. Add e.g. hosted1 (keep local)." ;;
esac

for n in 1 2 3; do
    case ",$providers," in *,hosted$n,*) ;; *) continue ;; esac
    key_file="deploy/secrets/hosted${n}_api_key"
    # bootstrap-secrets.sh writes a 48-character alphanumeric placeholder. A real key that
    # happens to look like that is possible but unlikely; this warns rather than refuses.
    if [ -f "$key_file" ] && grep -Eqx '[A-Za-z0-9]{48}' "$key_file"; then
        info "WARNING: $key_file still looks like the bootstrap placeholder -- every request will 401."
    fi
done

say "Running checks inside the gateway"

GATEWAY_CID=$(docker compose -f "$COMPOSE_FILE" ps -q "$SERVICE")
[ -n "$GATEWAY_CID" ] || die "could not resolve the gateway container id"
docker cp "$DRIVER" "$GATEWAY_CID":/tmp/verify_phase_6.py || die "could not copy the driver into the gateway"

mkdir -p "$REPORT_DIR"
{
    echo "ADR-030 Phase 6 verification"
    echo "date:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commit:    $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "providers: $providers"
    echo
} > "$REPORT"

docker compose -f "$COMPOSE_FILE" exec -T \
    -e AURA_VERIFY_SKIP_429="${AURA_VERIFY_SKIP_429:-}" \
    -e AURA_VERIFY_429_MAX_REQUESTS="${AURA_VERIFY_429_MAX_REQUESTS:-12}" \
    -e AURA_VERIFY_TRACEBACK="${AURA_VERIFY_TRACEBACK:-}" \
    "$SERVICE" sh -lc 'PYTHONPATH=/app python /tmp/verify_phase_6.py' 2>&1 | tee -a "$REPORT"
status=${PIPESTATUS[0]}

sed -i 's/\x1b\[[0-9;]*m//g' "$REPORT" 2>/dev/null || true
docker exec "$GATEWAY_CID" rm -f /tmp/verify_phase_6.py >/dev/null 2>&1 || true

say "Result"
info "report written to $REPORT"
if [ "$status" -eq 0 ]; then
    info "Every scripted check passed. Complete check 11 by hand, then commit the report and mark"
    info "Phase 6 proven in docs/roadmaps/AURA_FREE_HOSTED_INFERENCE_PLAN.md and ADR-030."
else
    info "$status check(s) failed. ADR-030 stays 'built, not proven' until they pass."
fi
exit "$status"
