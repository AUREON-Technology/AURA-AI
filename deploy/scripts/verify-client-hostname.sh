#!/usr/bin/env bash
# Confirm the hostname baked into the Flutter client matches the live TLS cert.
# Run from anywhere; requires openssl and a reachable :443.
#
#   ./deploy/scripts/verify-client-hostname.sh
#   AURA_DOMAIN=aura.example.org ./deploy/scripts/verify-client-hostname.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=client_backend_url.sh
source "$SCRIPT_DIR/client_backend_url.sh"

ENV_FILE="$REPO_ROOT/deploy/.env"
BACKEND_URL="$(aura_resolve_client_backend_url "$ENV_FILE")"
aura_require_release_backend_url "$BACKEND_URL"

HOST="$(aura_backend_url_host "$BACKEND_URL")"
echo "Checking TLS for $HOST (SNI + connect $HOST:443)..."

CERT="$(echo | openssl s_client -servername "$HOST" -connect "$HOST:443" -verify_hostname "$HOST" 2>/dev/null | openssl x509 -noout -text 2>/dev/null || true)"
if [[ -z "$CERT" ]]; then
  echo "ERROR: could not fetch a certificate from $HOST:443." >&2
  echo "       On a shared host, host nginx must have a server_name and cert for $HOST." >&2
  echo "       See deploy/nginx/host-proxy.conf.example." >&2
  exit 1
fi

if ! echo "$CERT" | grep -Eq "DNS:${HOST}(,|$| )"; then
  # SAN lines look like: DNS:example.com, DNS:www.example.com
  if ! echo "$CERT" | grep -F "DNS:$HOST" >/dev/null; then
    echo "ERROR: certificate presented for SNI $HOST does not include that name as a SAN." >&2
    echo "$CERT" | grep -E "Subject:|DNS:" >&2 || true
    echo "       Reissue Let's Encrypt for -d $HOST and set nginx server_name to the same value." >&2
    exit 1
  fi
fi

echo "OK: certificate SAN covers $HOST."
echo "    Client BACKEND_URL must be exactly: $BACKEND_URL"
