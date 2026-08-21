#!/bin/sh
# Render Alertmanager receivers from env/secrets, then exec the binary.
#
# No URL and no SMTP → the committed silent receivers (valid, pages stay in AM).
# Never writes a fake webhook. Busybox/ash compatible (alertmanager image).
set -eu

TEMPLATE=${ALERTMANAGER_CONFIG_TEMPLATE:-/etc/alertmanager/alertmanager.yml}
OUT=${ALERTMANAGER_CONFIG_OUT:-/tmp/alertmanager.yml}

yaml_single_quote() {
    # YAML single-quoted: ' is escaped as ''.
    printf '%s' "$1" | sed "s/'/''/g"
}

read_url() {
    if [ -n "${ALERTMANAGER_WEBHOOK_URL:-}" ]; then
        printf '%s' "$ALERTMANAGER_WEBHOOK_URL"
        return
    fi
    file=${ALERTMANAGER_WEBHOOK_URL_FILE:-}
    if [ -z "$file" ] && [ -f /run/secrets/alertmanager_webhook_url ]; then
        file=/run/secrets/alertmanager_webhook_url
    fi
    if [ -n "$file" ] && [ -f "$file" ]; then
        # First non-empty, non-comment line.
        sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$file" | sed -n '/./{p;q;}'
    fi
}

read_critical_url() {
    if [ -n "${ALERTMANAGER_CRITICAL_WEBHOOK_URL:-}" ]; then
        printf '%s' "$ALERTMANAGER_CRITICAL_WEBHOOK_URL"
        return
    fi
    file=${ALERTMANAGER_CRITICAL_WEBHOOK_URL_FILE:-}
    if [ -n "$file" ] && [ -f "$file" ]; then
        sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$file" | sed -n '/./{p;q;}'
        return
    fi
    read_url
}

URL=$(read_url | tr -d '\r\n' || true)
CRITICAL_URL=$(read_critical_url | tr -d '\r\n' || true)

cp "$TEMPLATE" "$OUT"

inject_webhook() {
    name=$1
    url=$2
    [ -n "$url" ] || return 0
    escaped=$(yaml_single_quote "$url")
    awk -v name="$name" -v url="$escaped" '
        $0 == "  - name: " name {
            print
            print "    webhook_configs:"
            print "      - url: '\''" url "'\''"
            print "        send_resolved: true"
            next
        }
        { print }
    ' "$OUT" > "$OUT.awk"
    mv "$OUT.awk" "$OUT"
}

inject_email() {
    name=$1
    to=$2
    [ -n "$to" ] || return 0
    escaped=$(yaml_single_quote "$to")
    awk -v name="$name" -v to="$escaped" '
        $0 == "  - name: " name {
            print
            print "    email_configs:"
            print "      - to: '\''" to "'\''"
            print "        send_resolved: true"
            next
        }
        { print }
    ' "$OUT" > "$OUT.awk"
    mv "$OUT.awk" "$OUT"
}

inject_webhook default "$URL"
inject_webhook critical "$CRITICAL_URL"

if [ -n "${ALERTMANAGER_SMTP_SMARTHOST:-}" ] && [ -n "${ALERTMANAGER_SMTP_FROM:-}" ] && [ -n "${ALERTMANAGER_SMTP_TO:-}" ]; then
    smarthost=$(yaml_single_quote "$ALERTMANAGER_SMTP_SMARTHOST")
    from=$(yaml_single_quote "$ALERTMANAGER_SMTP_FROM")
    smtp_user=$(yaml_single_quote "${ALERTMANAGER_SMTP_AUTH_USERNAME:-}")
    smtp_pass=$(yaml_single_quote "${ALERTMANAGER_SMTP_AUTH_PASSWORD:-}")
    smtp_tls=${ALERTMANAGER_SMTP_REQUIRE_TLS:-}
    awk -v smarthost="$smarthost" -v from="$from" -v user="$smtp_user" -v pass="$smtp_pass" -v tls="$smtp_tls" '
        BEGIN { done = 0 }
        /^global:/ { print; in_global = 1; next }
        in_global && /^[^ ]/ {
            if (!done) {
                print "  smtp_smarthost: '\''" smarthost "'\''"
                print "  smtp_from: '\''" from "'\''"
                if (user != "") {
                    print "  smtp_auth_username: '\''" user "'\''"
                }
                if (pass != "") {
                    print "  smtp_auth_password: '\''" pass "'\''"
                }
                if (tls != "") {
                    print "  smtp_require_tls: " tls
                }
                done = 1
            }
            in_global = 0
        }
        { print }
    ' "$OUT" > "$OUT.awk"
    mv "$OUT.awk" "$OUT"
    inject_email default "$ALERTMANAGER_SMTP_TO"
    inject_email critical "${ALERTMANAGER_SMTP_TO_CRITICAL:-$ALERTMANAGER_SMTP_TO}"
fi

# Image CMD is `--config.file=/etc/alertmanager/alertmanager.yml` (the silent
# template). Always load the rendered file so receivers actually fire.
exec /bin/alertmanager --config.file="$OUT"
