# Resolve and validate the Flutter client's compile-time BACKEND_URL.
# Canonical host is AURA_DOMAIN in deploy/.env (or the environment).
# Sourced by deploy_client.sh and verify-client-hostname.sh.
#
# Usage:
#   source deploy/scripts/client_backend_url.sh
#   url="$(aura_resolve_client_backend_url /path/to/deploy/.env)"
#   aura_require_release_backend_url "$url"

aura_read_env_key() {
  local key="$1"
  local file="$2"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r' || true
}

aura_backend_url_host() {
  local url="$1"
  echo "$url" | sed -E 's#^https?://##' | cut -d/ -f1 | cut -d: -f1
}

aura_resolve_client_backend_url() {
  local env_file="${1:-}"
  local url="${BACKEND_URL:-}"
  local domain="${AURA_DOMAIN:-}"

  if [[ -n "$env_file" && -f "$env_file" ]]; then
    if [[ -z "$url" ]]; then
      url="$(aura_read_env_key BACKEND_URL "$env_file")"
    fi
    if [[ -z "$domain" ]]; then
      domain="$(aura_read_env_key AURA_DOMAIN "$env_file")"
    fi
  fi

  if [[ -z "$url" && -n "$domain" ]]; then
    url="https://${domain}"
  fi

  echo "$url"
}

aura_require_release_backend_url() {
  local url="$1"
  if [[ -z "$url" ]]; then
    echo "ERROR: BACKEND_URL is empty. Set AURA_DOMAIN (or BACKEND_URL) in deploy/.env." >&2
    echo "       Debug builds default to http://localhost:8000; production must be injected at compile time." >&2
    return 1
  fi

  case "$url" in
    https://*) ;;
    *)
      echo "ERROR: Release BACKEND_URL must be https://... (got: $url)." >&2
      echo "       Android and iOS block cleartext HTTP by default." >&2
      return 1
      ;;
  esac

  local host
  host="$(aura_backend_url_host "$url")"

  case "$host" in
    ""|localhost|127.0.0.1|::1)
      echo "ERROR: Release BACKEND_URL must not target localhost (got: $url)." >&2
      return 1
      ;;
    aura.example.com|example.com|your-domain|your-vps-domain|*.your-domain)
      echo "ERROR: AURA_DOMAIN is still the placeholder '$host'. Set a real DNS name in deploy/.env." >&2
      return 1
      ;;
  esac

  if echo "$host" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "ERROR: Release BACKEND_URL must be a hostname, not a raw IP ($host)." >&2
    echo "       Use the VPS nip.io name (https://<ip>.nip.io) when there is no domain." >&2
    return 1
  fi

  return 0
}
