#!/usr/bin/env bash
# Installed and run by the broker, as root, after every boot and before any agent command.
#   fence.sh <allowed host>...
# Gives the agent user loopback and an allowlisting proxy, and nothing else, then proves it.
# Only after every proof does it write the ready mark that run.sh and file.sh require. Exits
# non-zero if anything fails; the broker then destroys the sandbox.
#
# Never write, as root, to a path under the agent's home: the agent owns it and could have left
# a symlink there that survived in a checkpoint. Never let anything the agent wrote there take
# part in a proof (shell startup files, curl's configuration).
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

AGENT_UID=10001
AGENT=aura
CONF=/etc/aura-proxy
LIB=/usr/local/lib/aura
STATE=/var/lib/aura
PROXY=http://127.0.0.1:8888

[ "$(id -u)" = 0 ] || exit 2
[ "$#" -gt 0 ] || exit 2
for host in "$@"; do
    [[ "$host" =~ ^[a-z0-9.-]+$ ]] || exit 2
done

# Withdraw the ready mark first: from here on nothing runs until the end of this script.
install -d -m 0700 -o root -g root "$STATE" "$STATE/staging" "$STATE/cancelled"
rm -f "$STATE/ready"

for tool in nft tinyproxy setpriv prlimit curl timeout pkill pgrep realpath ss python3; do
    command -v "$tool" >/dev/null || { echo "aura: missing $tool" >&2; exit 3; }
done
id "$AGENT" >/dev/null 2>&1 || useradd -m -u "$AGENT_UID" -s /bin/bash "$AGENT"
[ "$(id -u "$AGENT")" = "$AGENT_UID" ] || exit 3
pkill -KILL -u "$AGENT_UID" || true
find "$STATE/staging" "$STATE/cancelled" -mindepth 1 -delete

# Nothing owned by root may be writable by everyone (the base image ships one such folder).
find / -xdev \( -path /tmp -o -path /var/tmp -o -path /run/lock \) -prune -o \
    -user root -perm -0002 \( -type f -o -type d \) -exec chmod o-w {} +

# Narrow what an unprivileged process can do to the kernel and to its neighbours: no attaching
# to other processes, no user namespaces. Where the knob exists, it must take.
restrict() {
    [ -e "$1" ] || return 0
    echo "$2" >"$1"
    [ "$(cat "$1")" = "$2" ] || { echo "aura: cannot set $1" >&2; exit 6; }
}
restrict /proc/sys/kernel/yama/ptrace_scope 2
restrict /proc/sys/user/max_user_namespaces 0

install -d -m 0755 "$CONF"
: >"$CONF/filter"
for host in "$@"; do
    printf '^%s$\n' "${host//./\\.}" >>"$CONF/filter"
done
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
chmod 0644 "$CONF/filter" "$CONF/tinyproxy.conf"
install -d -m 0750 -o tinyproxy -g tinyproxy /var/log/aura-proxy

# A privileged service listening on TCP would be reachable over loopback. The agent's processes
# are gone, so anything listening now other than our proxy (restarted below) is unexpected.
pkill -x tinyproxy || true
for _ in $(seq 20); do pgrep -x tinyproxy >/dev/null || break; sleep 0.1; done
listeners=$(ss -H -lnt | awk '{print $4}' | tr '\n' ' ')
[ -z "$listeners" ] || { echo "aura: unexpected listening sockets: $listeners" >&2; exit 14; }
# Datagram sockets the platform holds open are closed to the agent, loopback included.
udp_ports=$(ss -H -lnu | awk '{print $4}' | sed 's/.*://' | grep -E '^[0-9]+$' | sort -u | paste -sd, - || true)
udp_rule=""
[ -z "$udp_ports" ] || udp_rule="meta skuid $AGENT_UID udp dport { $udp_ports } reject"

# Default deny for everything leaving the VM, IPv4 and IPv6. Native nftables: this kernel has
# neither the xt_owner nor the xt REJECT module. The agent user is decided first -- loopback
# only, and no DNS even there. Root keeps access (the platform's own processes). Loading the
# whole table is one transaction.
nft -f - <<EOF
table inet aura
delete table inet aura
table inet aura {
    chain output {
        type filter hook output priority 0; policy drop;
        meta skuid $AGENT_UID meta l4proto { tcp, udp } th dport 53 reject
        $udp_rule
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

setsid tinyproxy -c "$CONF/tinyproxy.conf" </dev/null
ready=no
for _ in $(seq 40); do
    if (exec 3<>/dev/tcp/127.0.0.1/8888) 2>/dev/null; then ready=yes; break; fi
    sleep 0.25
done
[ "$ready" = yes ] || { echo "aura: the proxy did not start" >&2; exit 4; }

# As the agent, with nothing from the agent's home in play and no input.
as_agent() {
    setpriv --reuid="$AGENT_UID" --regid="$AGENT_UID" --init-groups --no-new-privs \
        env -i HOME=/nonexistent PATH=/usr/bin:/bin HTTPS_PROXY="$PROXY" https_proxy="$PROXY" \
        bash --norc --noprofile -c "$1" </dev/null
}
# The workspace is made by the agent, never by root (see the note at the top).
as_agent "mkdir -p /home/$AGENT/workspace" || exit 5

sockets=$(as_agent 'find / /run -xdev -type s -writable 2>/dev/null; true' | sort -u | tr '\n' ' ')
[ -z "$sockets" ] || { echo "aura: the agent can reach unix sockets: $sockets" >&2; exit 15; }

# Proof, as the agent. The allowed request comes first: without it, every refusal below could
# just be a dead network. `curl -q` ignores any configuration file.
as_agent 'curl -q -sSf --max-time 20 -o /dev/null https://pypi.org/simple/pip/' ||
    as_agent 'curl -q -sSf --max-time 20 -o /dev/null https://github.com/robots.txt' ||
    { echo "aura: no allowed host is reachable" >&2; exit 10; }
if as_agent 'curl -q -sSf --max-time 15 -o /dev/null https://example.com' 2>/dev/null; then
    echo "aura: the proxy let a refused host through" >&2; exit 11
fi
if as_agent 'curl -q -sS --noproxy "*" --max-time 8 -o /dev/null https://1.1.1.1' 2>/dev/null; then
    echo "aura: a direct connection got out" >&2; exit 12
fi
if as_agent 'timeout 5 bash --norc --noprofile -c "</dev/tcp/8.8.8.8/53"' 2>/dev/null; then
    echo "aura: DNS got out" >&2; exit 13
fi
# The system-call filter must both let a command run and refuse the channel nftables cannot see.
as_agent "/usr/bin/python3 -I $LIB/confine.py -- true" || { echo "aura: cannot confine commands" >&2; exit 16; }
if as_agent "/usr/bin/python3 -I $LIB/confine.py -- /usr/bin/python3 -I -c 'import socket; socket.socket(40, socket.SOCK_STREAM)'" 2>/dev/null; then
    echo "aura: the hypervisor socket is not blocked" >&2; exit 17
fi

# Everything proved. The mark names this boot, so a copy restored from a checkpoint is void.
cat /proc/sys/kernel/random/boot_id >"$STATE/ready"
chmod 0600 "$STATE/ready"
echo "aura: fence ready"
