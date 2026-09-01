#!/usr/bin/env bash
#
# Expand or renew the production TLS certificate to include both AURA_DOMAIN and
# AURA_DEV_DOMAIN (fixes NET::ERR_CERT_COMMON_NAME_INVALID on the dev hostname).
#
# Run on the VPS from the deploy/ directory - not from a Windows workstation:
#   cd /opt/aura/deploy && ./scripts/renew-tls-dual-san.sh
#
# Tries webroot validation first (nginx stays up). If that fails, stops nginx and
# uses standalone on port 80, then starts nginx again.
#
set -euo pipefail

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

DEPLOY_DIR="$(pwd)"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"
ENV_FILE="$DEPLOY_DIR/.env"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot}"
CERT_VOLUME="${AURA_NGINX_CERT_VOLUME:-aura_nginx_certs}"

[ -f "$COMPOSE_FILE" ] || die "run from deploy/ (expected docker-compose.prod.yml in \$PWD)"
[ -f "$ENV_FILE" ] || die "missing $ENV_FILE"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

read_env_key() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" 2>/dev/null | head -1 | tr -d "[:space:]\"'" | tr -d '\r' || true
}

AURA_DOMAIN="${AURA_DOMAIN:-$(read_env_key AURA_DOMAIN)}"
AURA_DEV_DOMAIN="${AURA_DEV_DOMAIN:-$(read_env_key AURA_DEV_DOMAIN)}"

[ -n "$AURA_DOMAIN" ] || die "AURA_DOMAIN is unset in $ENV_FILE"
[ -n "$AURA_DEV_DOMAIN" ] || die "AURA_DEV_DOMAIN is unset in $ENV_FILE (required for dual-SAN renewal)"

CERTBOT_EMAIL="${CERTBOT_EMAIL:-$(read_env_key CERTBOT_EMAIL)}"
if [ -z "$CERTBOT_EMAIL" ]; then
  first_admin="$(read_env_key SUPERADMIN_EMAILS)"
  CERTBOT_EMAIL="${first_admin%%,*}"
fi

EMAIL_ARGS=()
if [ -n "$CERTBOT_EMAIL" ]; then
  EMAIL_ARGS=(-m "$CERTBOT_EMAIL" --no-eff-email)
else
  info "CERTBOT_EMAIL unset; using --register-unsafely-without-email"
  EMAIL_ARGS=(--register-unsafely-without-email)
fi

DC=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

read_pem_text() {
  docker run --rm \
    -v "${CERT_VOLUME}:/etc/letsencrypt" \
    alpine/openssl:latest \
    x509 -in "/etc/letsencrypt/live/$AURA_DOMAIN/fullchain.pem" -noout -text 2>/dev/null || true
}

pem_has_dev_san() {
  local cert
  cert="$(read_pem_text)"
  [ -n "$cert" ] && echo "$cert" | grep -F "DNS:$AURA_DEV_DOMAIN" >/dev/null
}

run_webroot() {
  docker run --rm \
    -v "${CERT_VOLUME}:/etc/letsencrypt" \
    -v "$DEPLOY_DIR/nginx/www:/var/www/certbot" \
    "$CERTBOT_IMAGE" certonly --expand --cert-name "$AURA_DOMAIN" \
    --non-interactive \
    --webroot -w /var/www/certbot \
    -d "$AURA_DOMAIN" -d "$AURA_DEV_DOMAIN" \
    --agree-tos "${EMAIL_ARGS[@]}"
}

run_standalone() {
  say "Stopping nginx for standalone ACME challenge"
  "${DC[@]}" stop nginx
  cleanup() {
    say "Starting nginx"
    "${DC[@]}" start nginx || true
  }
  trap cleanup EXIT

  docker run --rm -p 80:80 \
    -v "${CERT_VOLUME}:/etc/letsencrypt" \
    "$CERTBOT_IMAGE" certonly --expand --cert-name "$AURA_DOMAIN" \
    --non-interactive \
    --standalone \
    -d "$AURA_DOMAIN" -d "$AURA_DEV_DOMAIN" \
    --agree-tos "${EMAIL_ARGS[@]}"

  trap - EXIT
  cleanup
}

verify_dev_san() {
  say "Verifying certificate SAN for $AURA_DEV_DOMAIN"
  local cert
  cert="$(read_pem_text)"
  if [ -z "$cert" ]; then
    die "could not read fullchain.pem for $AURA_DOMAIN from cert volume $CERT_VOLUME"
  fi
  echo "$cert" | grep -E "Subject:|DNS:" || true
  if ! echo "$cert" | grep -F "DNS:$AURA_DEV_DOMAIN" >/dev/null; then
    die "certificate does not include DNS:$AURA_DEV_DOMAIN - expand may have failed"
  fi
  info "OK: SAN covers $AURA_DEV_DOMAIN"
}

say "Dual-SAN TLS renewal"
info "deploy dir:  $DEPLOY_DIR"
info "prod host:   $AURA_DOMAIN"
info "dev host:    $AURA_DEV_DOMAIN"
info "cert volume: $CERT_VOLUME"

mkdir -p "$DEPLOY_DIR/nginx/www"

if pem_has_dev_san; then
  info "certificate already includes $AURA_DEV_DOMAIN — skipping certbot"
elif run_webroot; then
  info "webroot issuance succeeded"
else
  info "webroot failed; falling back to standalone"
  run_standalone
fi

say "Reloading nginx"
"${DC[@]}" up -d --force-recreate --no-deps nginx
sleep 3

verify_dev_san

say "Done"
info "If https://$AURA_DEV_DOMAIN still returns 502, start the aura-dev stack (gateway must be running):"
info "  docker compose -p aura-dev -f docker-compose.dev.yml --env-file .env.development up -d"
