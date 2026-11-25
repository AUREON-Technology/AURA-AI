#!/usr/bin/env bash
# Phase 0 proof for Railway Sandboxes (docs/plans/railway-sandboxes-plan.md).
# Runs INSIDE a throwaway sandbox, started by run.py. Prints names and verdicts only:
# never the value of an environment variable or the content of a file that may hold a secret.
#
#   guest.sh inspect   what the VM looks like before anything is changed
#   guest.sh setup     unprivileged user, allowlisting proxy, default-deny firewall
#   guest.sh probe     checks S1-S3, one "RESULT|id|PASS or FAIL|detail" line each
set -u

AGENT_UID=10001
AGENT=aura
CONF=/etc/aura-proxy
PROXY=http://127.0.0.1:8888
# The same seven hosts as deploy/sandbox/filter.
ALLOWED='^github\.com$
^api\.github\.com$
^codeload\.github\.com$
^objects\.githubusercontent\.com$
^pypi\.org$
^files\.pythonhosted\.org$
^registry\.npmjs\.org$'

result() { printf 'RESULT|%s|%s|%s\n' "$1" "$2" "$3"; }

inspect() {
    echo "whoami: $(id)"
    grep -E '^(PRETTY_NAME)=' /etc/os-release
    echo "kernel: $(uname -r)"
    echo "cpus: $(nproc)  mem: $(awk '/MemTotal/ {print $2}' /proc/meminfo) kB"
    df -h / | tail -1
    for tool in git python3 node npm curl gcc iptables ip6tables nft tinyproxy sudo setpriv ss ip; do
        printf '%s: %s\n' "$tool" "$(command -v "$tool" || echo absent)"
    done
    echo "env names: $(env -0 | cut -z -d= -f1 | tr '\0' ' ')"
    echo "pid1: $(tr '\0' ' ' </proc/1/cmdline | cut -c1-120)"
    echo "pid1 env names: $(cut -z -d= -f1 /proc/1/environ 2>/dev/null | tr '\0' ' ')"
    echo "resolv.conf: $(grep -E '^nameserver' /etc/resolv.conf | tr '\n' ' ')"
}

setup() {
    set -e
    [ "$(id -u)" = 0 ] || { echo "setup needs root"; exit 1; }
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    # The image keeps python3 and node under /root (mise), where the agent user cannot reach.
    apt-get install -y -qq tinyproxy curl python3 iproute2 util-linux >/dev/null

    id "$AGENT" >/dev/null 2>&1 || useradd -m -u "$AGENT_UID" -s /bin/bash "$AGENT"

    # The image ships this root-owned folder world-writable. Nothing the agent can write may
    # belong to root (found by the first proof run).
    [ -d /var/lib/pebble/default ] && chmod o-w /var/lib/pebble/default

    mkdir -p "$CONF" /var/log/aura-proxy
    printf '%s\n' "$ALLOWED" >"$CONF/filter"
    cat >"$CONF/tinyproxy.conf" <<EOF
User tinyproxy
Group tinyproxy
Port 8888
Listen 127.0.0.1
Allow 127.0.0.1
Timeout 600
MaxClients 40
LogLevel Notice
LogFile "/var/log/aura-proxy/tinyproxy.log"
FilterDefaultDeny Yes
FilterURLs Off
FilterExtended On
FilterCaseSensitive Off
Filter "$CONF/filter"
ConnectPort 443
DisableViaHeader Yes
EOF
    chown -R tinyproxy:tinyproxy /var/log/aura-proxy
    chmod 0644 "$CONF/filter" "$CONF/tinyproxy.conf"
    systemctl disable --now tinyproxy >/dev/null 2>&1 || true
    pkill -x tinyproxy >/dev/null 2>&1 || true
    sleep 1
    tinyproxy -c "$CONF/tinyproxy.conf"

    # Default deny for everything that leaves the VM, IPv4 and IPv6 (family inet). Native
    # nftables: this kernel has no xt_owner or xt REJECT modules, so iptables rules fail here.
    # The agent user is decided first: loopback only, and no DNS even there, so a local
    # resolver cannot be used as a tunnel. Root keeps access (the platform's own processes and
    # this setup run as root). The one-transaction load replaces any earlier table.
    nft -f - <<EOF
table inet aura
delete table inet aura
table inet aura {
    chain output {
        type filter hook output priority 0; policy drop;
        meta skuid $AGENT_UID meta l4proto { tcp, udp } th dport 53 reject
        meta skuid $AGENT_UID oifname "lo" accept
        meta skuid $AGENT_UID reject
        ct state established,related accept
        oifname "lo" accept
        meta l4proto ipv6-icmp accept
        meta skuid 0 accept
        meta skuid $(id -u tinyproxy) tcp dport 443 accept
        meta skuid $(id -u tinyproxy) meta l4proto { tcp, udp } th dport 53 accept
    }
}
EOF
    echo "setup ok"
}

# Run a command exactly as the broker would: agent uid, no new privileges, clean environment.
as_agent() {
    setpriv --reuid="$AGENT_UID" --regid="$AGENT_UID" --init-groups --no-new-privs \
        env -i HOME="/home/$AGENT" PATH=/usr/local/bin:/usr/bin:/bin \
        HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" http_proxy="$PROXY" https_proxy="$PROXY" \
        NO_PROXY=localhost,127.0.0.1 bash -c "$1"
}

# expect_ok/expect_fail <id> <description> <command run as the agent>
expect_ok() {
    if as_agent "$3" >/dev/null 2>&1; then result "$1" PASS "$2"; else result "$1" FAIL "$2 (was refused)"; fi
}
expect_fail() {
    if as_agent "$3" >/dev/null 2>&1; then result "$1" FAIL "$2 (succeeded)"; else result "$1" PASS "$2"; fi
}

probe() {
    # Positive control: if the agent cannot run anything, every "refused" below would be hollow.
    if ! as_agent true; then result S1 FAIL "could not run any command as the agent"; exit 1; fi

    # -- S1: unprivileged and unable to change the fence --------------------------------------
    expect_ok S1 "commands run as uid $AGENT_UID" "[ \"\$(id -u)\" = $AGENT_UID ]"
    expect_ok S1 "no new privileges is set" "grep -q '^NoNewPrivs:[[:space:]]*1' /proc/self/status"
    expect_ok S1 "no effective capabilities" "grep -qE '^CapEff:[[:space:]]*0+\$' /proc/self/status"
    expect_fail S1 "sudo does not give root" "sudo -n true"
    expect_fail S1 "su does not give root" "echo | su -c true root"
    expect_fail S1 "cannot read the firewall" "nft list ruleset | grep -q aura"
    expect_fail S1 "cannot flush the firewall" "nft flush ruleset"
    expect_fail S1 "cannot edit the allowlist" "echo '.*' >> $CONF/filter"
    proxy_pid=$(pgrep -x tinyproxy | head -1)
    if [ -n "$proxy_pid" ]; then result S1 PASS "control: the proxy is running"; else result S1 FAIL "the proxy is not running"; fi
    expect_fail S1 "cannot stop the proxy" "kill ${proxy_pid:-1}"
    expect_fail S1 "cannot read root's process environment" "cat /proc/1/environ"
    expect_fail S1 "a new user namespace gives no way out" \
        "unshare -Urn bash -c 'curl -sS --noproxy \"*\" --max-time 8 https://1.1.1.1'"
    writable=$(as_agent "find / -xdev \\( -type f -o -type d \\) -writable -user root \
        -not -path '/proc/*' -not -path '/tmp*' -not -path '/var/tmp*' -not -path '/dev/*' \
        -not -path '/run/lock*' 2>/dev/null | head -20" | tr '\n' ' ')
    if [ -z "$writable" ]; then result S1 PASS "no root-owned path is writable"; else result S1 FAIL "writable root paths: $writable"; fi
    sockets=$(as_agent "find /run /var/run -type s -writable 2>/dev/null" | sort -u | tr '\n' ' ')
    result S1 INFO "unix sockets the agent can write: ${sockets:-none}"

    # -- S2: outbound reaches the allowlist only -----------------------------------------------
    expect_ok S2 "pypi.org through the proxy" "curl -sSf --max-time 20 -o /dev/null https://pypi.org/simple/pip/"
    expect_ok S2 "github.com through the proxy" "curl -sSf --max-time 20 -o /dev/null https://github.com/robots.txt"
    expect_ok S2 "npm registry through the proxy" "curl -sSf --max-time 20 -o /dev/null https://registry.npmjs.org/left-pad"
    expect_fail S2 "example.com refused by the proxy" "curl -sSf --max-time 20 https://example.com"
    expect_fail S2 "look-alike host refused" "curl -sSf --max-time 20 https://github.com.example.com"
    expect_fail S2 "plain HTTP to another host refused" "curl -sSf --max-time 20 http://example.com"
    expect_fail S2 "non-443 CONNECT refused" "curl -sSf --max-time 15 -p -x $PROXY http://github.com:22"
    expect_fail S2 "proxy refuses an IP literal" "curl -sSf --max-time 15 https://1.1.1.1"
    expect_fail S2 "proxy refuses the metadata address" "curl -sSf --max-time 15 -p -x $PROXY http://169.254.169.254:443/"
    expect_fail S2 "direct HTTPS to an allowed host refused" "curl -sSf --noproxy '*' --max-time 10 https://pypi.org/simple/pip/"
    expect_fail S2 "direct HTTPS to an IP refused" "curl -sS --noproxy '*' --max-time 10 https://1.1.1.1"
    expect_fail S2 "direct IPv6 refused" "curl -sS --noproxy '*' --max-time 10 'https://[2606:4700:4700::1111]'"
    expect_fail S2 "no DNS through the system resolver" "getent ahosts example.com"
    for target in $(grep -E '^nameserver' /etc/resolv.conf | awk '{print $2}'); do
        query="import socket
s = socket.socket(socket.AF_INET6 if ':' in '$target' else socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(5)
s.sendto(bytes.fromhex('123401000001000000000000076578616d706c6503636f6d0000010001'), ('$target', 53))
s.recvfrom(512)"
        # Control: the same query must be answered for root, or the refusal below proves nothing.
        if /usr/bin/python3 -c "$query" >/dev/null 2>&1; then
            result S2 PASS "control: root gets a DNS answer from $target"
        else
            result S2 FAIL "control: root got no DNS answer from $target"
        fi
        expect_fail S2 "no raw UDP DNS to $target" "/usr/bin/python3 -c \"$query\""
    done
    expect_fail S2 "no raw TCP to port 22" "timeout 8 bash -c '</dev/tcp/140.82.121.4/22'"
    expect_fail S2 "no ping" "ping -c1 -W5 1.1.1.1"
    expect_fail S2 "no raw sockets" "python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_RAW, 1)'"

    # -- S3: nothing private, no platform credential -------------------------------------------
    for host in postgres.railway.internal redis.railway.internal aura-ai.railway.internal; do
        if getent ahosts "$host" >/dev/null 2>&1; then result S3 FAIL "root resolves $host"; else result S3 PASS "$host does not resolve"; fi
    done
    code=$(curl -s --noproxy '*' --max-time 8 -o /dev/null -w '%{http_code}' http://169.254.169.254/ 2>/dev/null || true)
    if [ "${code:-000}" = 000 ]; then result S3 PASS "no metadata endpoint"; else result S3 FAIL "metadata endpoint answers HTTP $code to root"; fi
    names=$( { env -0; cat /proc/[0-9]*/environ 2>/dev/null; } | cut -z -d= -f1 | tr '\0' '\n' \
        | grep -E 'RAILWAY|TOKEN|SECRET|PASSWORD|API_KEY|DATABASE|REDIS' | sort -u | tr '\n' ' ')
    result S3 INFO "credential-like variable names in any process: ${names:-none}"
    files=$(grep -rIlE --exclude=guest.sh --exclude=guest.b64 'RAILWAY_(API_)?TOKEN|Project-Access-Token' \
        /etc /run /root /var/lib 2>/dev/null | head -10 | tr '\n' ' ')
    result S3 INFO "files mentioning a Railway token: ${files:-none}"
    result S3 INFO "addresses: $(ip -o addr show | awk '{print $2"="$4}' | tr '\n' ' ')"
    result S3 INFO "listening: $(ss -H -lntup | awk '{print $1":"$5" "$7}' | tr '\n' ' ')"
}

case "${1:-}" in
    inspect) inspect ;;
    setup) setup ;;
    probe) probe ;;
    *) echo "usage: guest.sh inspect|setup|probe" >&2; exit 2 ;;
esac
