#!/usr/bin/env bash
# Run one agent command, confined. Started by the broker as root.
#   run.sh <command id> <timeout seconds> -- <argv>...
#   run.sh --cancel <command id>
#
# The command runs as the unprivileged agent user in its own cgroup. The deadline is enforced
# here, inside the VM (Railway's client deadline does not reliably stop a process), and every
# process the command started -- detached ones included -- ends with it.
#
# AURA_MARK, from the broker, prefixes what this script reports about itself. The command cannot
# read root's environment, so it cannot forge those reports.
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

AGENT_UID=10001
LIB=/usr/local/lib/aura
STATE=/var/lib/aura
PROXY=http://127.0.0.1:8888
MARK=${AURA_MARK:-unset}
unset AURA_MARK

group_of() { printf '/sys/fs/cgroup/aura-%s' "$1"; }

end_group() {
    local group
    group=$(group_of "$1")
    [ -d "$group" ] || return 0
    for _ in $(seq 50); do
        # Written every time: a process may have joined since the last write.
        echo 1 >"$group/cgroup.kill" 2>/dev/null
        rmdir "$group" 2>/dev/null && return 0
        sleep 0.1
    done
    return 1
}

valid_id() { [[ "$1" =~ ^[0-9a-f]{32}$ ]]; }

if [ "${1:-}" = --cancel ]; then
    valid_id "${2:-}" || exit 2
    # The note stops a command that has not started yet; the group ends one that has.
    : >"$STATE/cancelled/$2" || exit 1
    end_group "$2"
    exit
fi

id=${1:-}
limit=${2:-}
valid_id "$id" || exit 2
[[ "$limit" =~ ^[1-9][0-9]*$ ]] || exit 2
[ "${3:-}" = -- ] || exit 2
shift 3
[ "$#" -gt 0 ] || exit 2

# Fail closed: no proven fence, no command.
if [ ! -f "$STATE/ready" ] || [ "$(cat "$STATE/ready")" != "$(cat /proc/sys/kernel/random/boot_id)" ] ||
    ! nft list table inet aura >/dev/null 2>&1 ||
    ! pgrep -u aura-proxy -f "$LIB/proxy.py" >/dev/null; then
    echo "aura:fence-missing:$MARK" >&2
    exit 97
fi

group=$(group_of "$id")
mkdir "$group" && [ -e "$group/cgroup.kill" ] ||
    { echo "aura: cannot create the command's process group" >&2; exit 96; }
trap 'end_group "$id"' EXIT
trap 'exit 143' TERM INT HUP

started=$SECONDS
(
    echo "$BASHPID" >"$group/cgroup.procs" || exit 96
    # Checked after joining: a cancel either left its note first or will find this process.
    [ ! -e "$STATE/cancelled/$id" ] || exit 143
    # Hand down nothing but the three standard streams.
    for fd in /proc/"$BASHPID"/fd/*; do
        n=${fd##*/}
        [ "$n" -gt 2 ] && eval "exec $n>&-" 2>/dev/null
    done
    exec timeout --kill-after=5 "$limit" \
        setpriv --reuid="$AGENT_UID" --regid="$AGENT_UID" --init-groups --no-new-privs \
        prlimit --nproc=512 --nofile=4096 -- \
        env -i HOME=/home/aura USER=aura LOGNAME=aura LANG=C.UTF-8 PATH=/usr/local/bin:/usr/bin:/bin \
        HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" http_proxy="$PROXY" https_proxy="$PROXY" \
        NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
        /usr/bin/python3 -I "$LIB/confine.py" -- \
        bash --norc --noprofile -c 'cd /home/aura/workspace && exec "$@"' aura "$@"
) </dev/null &
wait "$!"
status=$?

if ! end_group "$id"; then
    echo "aura:cleanup-failed:$MARK" >&2
    exit 95
fi
if { [ "$status" = 124 ] || [ "$status" = 137 ]; } && [ $((SECONDS - started)) -ge "$limit" ]; then
    echo "aura:timeout:$MARK" >&2
fi
exit "$status"
