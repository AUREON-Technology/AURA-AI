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

generate postgres_password 40
generate redis_password 40
generate qdrant_api_key 48
generate jwt_secret 64
generate grafana_admin_password 24

echo
echo "Secrets are in $DIR with mode 600. They are gitignored."
echo "Back them up somewhere the backups themselves are not stored -- losing jwt_secret"
echo "invalidates every session; losing postgres_password locks you out of your own data."
