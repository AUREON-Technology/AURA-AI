#!/bin/sh
# Generate the secret files docker-compose.prod.yml and docker-compose.dev.yml expect.
#
# Run once on a new host. Nothing here has a default value anywhere in the stack -- SEC-08 was
# partly caused by POSTGRES_PASSWORD defaulting to a publicly-known string, so a deployment
# that forgets a variable runs with a known password rather than failing to start.
set -eu

DIR=$(dirname "$0")/../secrets
mkdir -p "$DIR"
chmod 700 "$DIR"

# The backup sidecar mounts only this subdirectory (docker-compose.prod.yml `backup:`), never
# the whole secrets directory -- see the compose comment. Files placed at the old flat
# location by earlier guides are moved, not copied, so no second copy of an S3 key lingers.
BACKUP_DIR="$DIR/backup"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
for name in backup_age_recipients.txt backup_s3_access_key backup_s3_secret_key rclone.conf; do
    if [ -f "$DIR/$name" ] && [ ! -e "$BACKUP_DIR/$name" ]; then
        mv "$DIR/$name" "$BACKUP_DIR/$name"
        echo "moved $name into secrets/backup/"
    fi
done

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
# Hosted open-weight provider slots (x-hosted-providers in both compose files; ADR-030).
# Placeholders for the same "the file must exist" reason -- overwrite each with the real
# provider key before listing its slot in AURA_LLM_PROVIDERS.
generate hosted1_api_key 48
generate hosted2_api_key 48
generate hosted3_api_key 48
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
# The aura-dev stack (docker-compose.dev.yml) shares this host and this directory, so it gets its
# own signing and data keys, generated exactly like the prod ones above (F-03, GAP-047). With
# prod's jwt_secret, any token dev minted -- or anyone who compromised a dev container could
# mint -- was a valid prod session; with prod's data_encryption_keys, dev could decrypt prod rows.
# Never copy a prod key into these files.
# dev_data_encryption_keys starts with only a fresh key, so dev rows sealed under the old shared
# key stop decrypting. Reseeding dev is the clean answer. If those rows must be kept, append
# prod's key as a second line (newest first), run scripts/reencrypt_fields.py in the dev gateway
# until it reports nothing left, then delete that line at once and restart dev -- dev holds the
# prod key for that window, which is the exposure this split exists to end.
generate dev_jwt_secret 64
generate_fernet_key dev_data_encryption_keys
# The rest of dev's secrets are its own files too (F46), so nothing a dev container holds is a
# credential of prod's. secrets_encryption_key: with prod's, dev could decrypt prod's stored
# GitHub tokens; a fresh dev key orphans GitHub accounts connected in dev before the split (they
# reconnect). The OAuth client secret and the hosted-provider keys are placeholders exactly like
# prod's: overwrite each with a dev credential (a separate GitHub App, a separate provider
# project), never by copying the prod file.
generate_fernet_key dev_secrets_encryption_key
generate dev_github_oauth_client_secret 40
generate dev_hosted1_api_key 48
generate dev_hosted2_api_key 48
generate dev_hosted3_api_key 48
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
