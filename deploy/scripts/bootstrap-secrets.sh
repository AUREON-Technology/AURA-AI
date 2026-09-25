#!/bin/sh
# Generate the secret files docker-compose.prod.yml expects.
#
# Run once on a new host. Nothing here has a default value anywhere in the stack -- SEC-08 was
# partly caused by POSTGRES_PASSWORD defaulting to a publicly-known string, so a deployment
# that forgets a variable runs with a known password rather than failing to start.
set -eu

DIR=$(dirname "$0")/../secrets
mkdir -p "$DIR"
chmod 700 "$DIR"

generate() {
    name=$1
    length=${2:-48}
    path="$DIR/$name"
    if [ -f "$path" ]; then
        echo "keeping existing $name"
        return
    fi
    # LC_ALL=C so tr does not choke on multibyte locales.
    LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$length" > "$path"
    chmod 600 "$path"
    echo "generated $name ($length chars)"
}

# A Fernet key (aura_core/adapters/secrets_crypto.py) is 32 random bytes, urlsafe-base64
# encoded -- generate()'s alnum-only alphabet would not decode as one. Rotating this after a
# GitHub identity has been stored orphans that row (its token becomes undecryptable), same
# caution as jwt_secret.
generate_fernet_key() {
    name=$1
    path="$DIR/$name"
    if [ -f "$path" ]; then
        echo "keeping existing $name"
        return
    fi
    openssl rand -base64 32 | tr '+/' '-_' > "$path"
    chmod 600 "$path"
    echo "generated $name (Fernet key)"
}

generate postgres_password 40
generate redis_password 40
generate qdrant_api_key 48
generate jwt_secret 64
generate grafana_admin_password 24
# Unused unless AURA_PROVIDER_HFSPACE_* is configured (see deploy/.env.example), but generated
# unconditionally like the secrets above -- gateway/workers declare it in their `secrets:` list,
# and docker compose refuses to start a service whose declared secret file does not exist.
generate hfspace_api_key 48
# Unused unless AURA_GITHUB_OAUTH_ENABLED is set, but present unconditionally for the same
# "the file must exist for the service to start" reason as hfspace_api_key.
generate_fernet_key secrets_encryption_key
# Encryption at rest for memory content and personal-OS record fields
# (aura_core/adapters/field_crypto.py). Unlike secrets_encryption_key this one is REQUIRED:
# the gateway and workers refuse to start in staging/production without it, and vps-update.sh
# runs this script on every deploy, so the first deploy after it was introduced creates it.
# Never regenerate it by deleting the file -- rotate by prepending a new key line (newest first),
# restarting, and running scripts/reencrypt_fields.py in the gateway (see its docstring).
generate_fernet_key data_encryption_keys
# Placeholder only -- unlike the secrets above, this one cannot be generated. Overwrite it with
# the real client secret from the GitHub App's settings page before setting
# AURA_GITHUB_OAUTH_ENABLED=true: printf '%s' "$CLIENT_SECRET" > "$DIR/github_oauth_client_secret"
generate github_oauth_client_secret 40

echo
echo "Secrets are in $DIR with mode 600. They are gitignored."
echo "Back them up somewhere the backups themselves are not stored -- losing jwt_secret"
echo "invalidates every session; losing postgres_password locks you out of your own data;"
echo "losing secrets_encryption_key orphans every connected GitHub account; losing"
echo "data_encryption_keys makes every stored memory and personal-OS record unreadable, and a"
echo "database backup is useless without it -- keep it apart from the database backups."
