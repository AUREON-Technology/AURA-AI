#!/usr/bin/env bash
# Move files between the root-only staging folder and the agent's workspace. Started as root.
#   file.sh write <relative path> <staged file>
#   file.sh read <relative path> <staged file> <max bytes>
#   file.sh discard <staged file>
#   file.sh usage
#   file.sh quiesce
# Exit: 91 the path leaves the workspace, 92 no such file, 93 over the limit, 97 no fence.
#
# Everything that opens a workspace file runs as the agent user, so a symlink the agent planted
# can reach only what the agent could already reach. Root only opens files in its own staging
# folder and hands the open descriptor down.
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

AGENT_UID=10001
LIB=/usr/local/lib/aura
STATE=/var/lib/aura
STAGING=$STATE/staging
WS=/home/aura/workspace

fenced() {
    [ -f "$STATE/ready" ] && [ "$(cat "$STATE/ready")" = "$(cat /proc/sys/kernel/random/boot_id)" ] || exit 97
}

end_group() {
    for _ in $(seq 50); do
        echo 1 >"$1/cgroup.kill" 2>/dev/null
        rmdir "$1" 2>/dev/null && return 0
        sleep 0.1
    done
    return 1
}

# Run a script as the agent, confined like a command and in a group of its own, so that nothing
# it starts outlives the file operation. Redirections apply to the descriptors handed down.
as_agent() {
    local group status
    group=/sys/fs/cgroup/aura-file-$$
    mkdir "$group" && [ -e "$group/cgroup.kill" ] || return 96
    (
        echo "$BASHPID" >"$group/cgroup.procs" || exit 96
        exec setpriv --reuid="$AGENT_UID" --regid="$AGENT_UID" --init-groups --no-new-privs \
            env -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 \
            /usr/bin/python3 -I "$LIB/confine.py" -- bash --norc --noprofile -c "$@"
    )
    status=$?
    end_group "$group" || return 95
    return "$status"
}

staged() { [[ "$1" =~ ^/var/lib/aura/staging/[0-9a-f]{32}$ ]] || exit 2; }

# Sets `target` to the real location of "$1" inside the workspace, or exits 91.
RESOLVE='ws=/home/aura/workspace
[ "$(realpath -m -- "$ws")" = "$ws" ] || exit 91
target=$(realpath -m -- "$ws/$1") || exit 91
case "$target" in "$ws"/*) ;; *) exit 91 ;; esac
'

case "${1:-}" in
    write)
        fenced
        staged "${3:-}"
        as_agent "$RESOLVE"'
[ -d "$target" ] && exit 91
mkdir -p -- "$(dirname -- "$target")" || exit 1
tmp=$(mktemp -- "$target.aura-XXXXXXXX") || exit 1
if [ -f "$target" ]; then chmod --reference="$target" -- "$tmp"; else chmod 0644 -- "$tmp"; fi
if cat >"$tmp" && mv -f -- "$tmp" "$target"; then exit 0; fi
rm -f -- "$tmp"
exit 1' aura "$2" <"$3"
        status=$?
        rm -f -- "$3"
        exit "$status"
        ;;
    read)
        fenced
        staged "${3:-}"
        [[ "${4:-}" =~ ^[0-9]+$ ]] || exit 2
        install -m 0600 /dev/null "$3" || exit 1
        as_agent "$RESOLVE"'
[ -f "$target" ] || exit 92
[ "$(stat -c %s -- "$target")" -le "$2" ] || exit 93
exec head -c "$(($2 + 1))" -- "$target"' aura "$2" "$4" </dev/null >"$3"
        status=$?
        # The file may have grown between the size check and the copy.
        if [ "$status" = 0 ] && [ "$(stat -c %s -- "$3")" -gt "$4" ]; then status=93; fi
        [ "$status" = 0 ] || rm -f -- "$3"
        exit "$status"
        ;;
    discard)
        staged "${2:-}"
        rm -f -- "$2"
        ;;
    usage)
        fenced
        # As root, which reads no file content and follows no link: the agent cannot hide bytes
        # from it by removing its own read permission.
        [ -d "$WS" ] && [ ! -L "$WS" ] || exit 91
        total=$(du -sb -- "$WS") || exit 1
        echo "${total%%[[:space:]]*}"
        ;;
    quiesce)
        for group in /sys/fs/cgroup/aura-*; do
            [ -d "$group" ] && end_group "$group"
        done
        for _ in $(seq 50); do
            pgrep -u "$AGENT_UID" >/dev/null || break
            pkill -KILL -u "$AGENT_UID"
            sleep 0.1
        done
        if pgrep -u "$AGENT_UID" >/dev/null; then exit 1; fi
        find "$STAGING" -mindepth 1 -delete || exit 1
        sync
        ;;
    *)
        exit 2
        ;;
esac
