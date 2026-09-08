#!/usr/bin/env bash
#
# Take a freshly provisioned Ubuntu host to the point where the AURA stack can be deployed.
#
# OS-level preparation only: hardware check, SSH hardening, firewall, swap, Docker. It stops
# before touching the application, because the application deploy has verification steps at each
# tier and belongs in docs/guides/provisioning-runbook.md where you can watch it.
#
# Run as root on the NEW host:
#
#     bash bootstrap-host.sh                    # report and prepare; asks before hardening SSH
#     bash bootstrap-host.sh --yes              # no prompts (for a host you have key access to)
#     bash bootstrap-host.sh --check-only       # report hardware and posture, change nothing
#
# THE ONE THING THIS WILL NOT DO TO YOU: it never disables SSH password authentication unless a
# usable public key is already installed for root. Locking yourself out of a remote server is a
# support ticket and a VNC session, and it is the single most common way this kind of script
# ruins someone's evening.

set -uo pipefail

YES=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y)     YES=1 ;;
        --check-only) CHECK_ONLY=1 ;;
        -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    \033[1;32mOK\033[0m   %s\n' "$*"; }
warn() { printf '    \033[1;33mWARN\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

ask() {
    [ "$YES" = 1 ] && return 0
    printf '    \033[1m%s [y/N]\033[0m ' "$1"
    read -r reply </dev/tty || return 1
    [[ "$reply" =~ ^[Yy]$ ]]
}

[ "$(id -u)" = 0 ] || die "run as root"

# -- 1. What did we actually buy? -------------------------------------------------------------
# Reported before anything else, because every decision below depends on it and because the plan
# name on an invoice is not a specification.

say "Hardware"
CORES=$(nproc)
MEM_GB=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
DISK_GB=$(df -BG --output=size / | tail -1 | tr -dc '0-9')
info "vCPU:  $CORES"
info "RAM:   ${MEM_GB} GiB"
info "Disk:  ${DISK_GB} GB on /"
info "Kernel: $(uname -r)     $(. /etc/os-release && echo "$PRETTY_NAME")"

# Thresholds from AI_HARDWARE_CAPACITY_REPORT.md. 16 GB is the floor and needs AURA_WORKERS=2;
# 24 GB is where the documented defaults simply work.
MEM_INT=${MEM_GB%.*}
if [ "$MEM_INT" -ge 24 ]; then
    ok "RAM is at or above the comfortable target -- defaults will work"
    SUGGESTED_WORKERS=4
elif [ "$MEM_INT" -ge 15 ]; then
    warn "RAM is at the floor. Set AURA_WORKERS=2 and expect no room for a second application."
    SUGGESTED_WORKERS=2
else
    warn "RAM is BELOW the floor for a 7B model (needs ~6 GB for the model alone)."
    warn "Either use a 3B model, or move inference off this host. See AI_HARDWARE_CAPACITY_REPORT.md"
    SUGGESTED_WORKERS=2
fi
[ "$CORES" -ge 4 ] && ok "vCPU count is adequate" || warn "fewer than 4 vCPU: generation will be slow"
[ "$DISK_GB" -ge 50 ] && ok "disk is adequate" || warn "under 50 GB: model weights plus images will be tight"

if [ "$CHECK_ONLY" = 1 ]; then
    say "Check-only; nothing changed"
    exit 0
fi

# -- 2. SSH, before anything else is exposed ---------------------------------------------------
# A fresh host with root+password on port 22 is under credential-stuffing attack within minutes
# of its first boot. This is the most urgent thing on the box.

say "SSH hardening"

KEYFILE=/root/.ssh/authorized_keys
HAVE_KEY=0
if [ -s "$KEYFILE" ] && grep -qE '^(ssh|ecdsa|sk-)' "$KEYFILE"; then
    HAVE_KEY=1
    ok "root has $(grep -cE '^(ssh|ecdsa|sk-)' "$KEYFILE") authorized key(s)"
else
    warn "no SSH key installed for root -- password login is the only way in"
    cat <<'EOF'

    Install one from your workstation, in another terminal, BEFORE continuing:

        ssh-keygen -t ed25519 -C "aura-admin"          # if you have no key yet
        ssh-copy-id root@169.58.188.43

    Then verify it works in a THIRD terminal (never close your current session
    until you have proved the new method works):

        ssh -o PasswordAuthentication=no root@169.58.188.43 true && echo KEY OK

EOF
fi

if [ "$HAVE_KEY" = 1 ]; then
    if ask "Disable SSH password authentication and root password login?"; then
        SSHD_DROPIN=/etc/ssh/sshd_config.d/99-aura-hardening.conf
        mkdir -p /etc/ssh/sshd_config.d
        cat > "$SSHD_DROPIN" <<'EOF'
# Installed by bootstrap-host.sh. Remove this file and restart ssh to revert.
PasswordAuthentication no
PermitRootLogin prohibit-password
KbdInteractiveAuthentication no
EOF
        if sshd -t 2>/dev/null; then
            systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null
            ok "password authentication disabled (key-only)"
            info "your CURRENT session stays open -- test a new one before logging out"
        else
            rm -f "$SSHD_DROPIN"
            warn "sshd rejected the config; reverted and changed nothing"
        fi
    else
        info "left as-is"
    fi
else
    warn "skipping password-auth lockout: no key present, and doing it anyway would lock you out"
fi

# fail2ban helps whether or not passwords are disabled: it also blunts key-scanning noise.
if ! command -v fail2ban-server >/dev/null 2>&1; then
    if ask "Install fail2ban to rate-limit SSH brute force?"; then
        apt-get update -qq && apt-get install -y -qq fail2ban && systemctl enable --now fail2ban \
            && ok "fail2ban running" || warn "fail2ban install failed"
    fi
else
    ok "fail2ban already installed"
fi

# -- 3. Firewall -------------------------------------------------------------------------------
# Default-deny inbound. Everything the stack needs internally talks over Docker's bridge, so only
# SSH and the web ports should ever be reachable from outside.

say "Firewall"
if ! command -v ufw >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq ufw
fi
if ufw status | grep -q "Status: active"; then
    ok "ufw already active"
else
    if ask "Enable ufw (allow 22/80/443, deny everything else inbound)?"; then
        ufw --force default deny incoming >/dev/null
        ufw --force default allow outgoing >/dev/null
        ufw allow 22/tcp  >/dev/null   # keep SSH first, always
        ufw allow 80/tcp  >/dev/null
        ufw allow 443/tcp >/dev/null
        ufw --force enable >/dev/null
        ok "ufw active"
        # Docker publishes ports by writing iptables rules that bypass ufw's INPUT chain. A
        # container published as 0.0.0.0:5432 is reachable from the internet even with ufw
        # "denying" it. The stack binds its data tier to the Docker network only, but this is
        # worth knowing before anyone adds a published port in a hurry.
        info "note: docker -p bypasses ufw. Publish to 127.0.0.1 unless you mean the internet."
    fi
fi

# -- 4. Swap -----------------------------------------------------------------------------------
# Insurance, not capacity. If the model is paging you have already lost -- but without swap,
# memory pressure is an instant OOM kill of whatever the kernel picks, which may not be the
# process that caused it.

say "Swap"
if swapon --show | grep -q .; then
    ok "swap present: $(swapon --show=SIZE --noheadings | tr -d ' ' | paste -sd+ -)"
else
    if ask "Create a 4 GB swapfile?"; then
        fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
        grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
        sysctl -qw vm.swappiness=10
        grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
        ok "4 GB swap active, swappiness=10"
    fi
fi

# -- 5. Docker ---------------------------------------------------------------------------------

say "Docker"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "docker $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1), compose plugin present"
else
    if ask "Install Docker Engine and the compose plugin from Docker's official repository?"; then
        apt-get update -qq
        apt-get install -y -qq ca-certificates curl gnupg
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
        chmod a+r /etc/apt/keyrings/docker.gpg
        . /etc/os-release
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
            > /etc/apt/sources.list.d/docker.list
        apt-get update -qq
        apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
            && systemctl enable --now docker \
            && ok "docker installed" || die "docker install failed"
    fi
fi

# Bound the journal and container logs. A chat backend under load writes a lot, and a full disk
# takes down Postgres in a way that looks like a database bug.
say "Log limits"
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
    cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" }
}
EOF
    systemctl restart docker 2>/dev/null
    ok "container logs capped at 50m x 3 per container"
else
    info "/etc/docker/daemon.json exists; left alone -- check it caps log size"
fi
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/99-aura.conf
systemctl restart systemd-journald 2>/dev/null && ok "journal capped at 500M"

# -- 6. Timezone and hostname ------------------------------------------------------------------

say "Housekeeping"
# Engineer-agent workspace roots. Created here rather than left to Docker, because a bind mount
# whose source is missing is created by the daemon as root-owned, and the sandbox containers run
# as an unprivileged uid (see deploy/sandbox/Dockerfile) that could then not write to it. Empty
# and harmless on a host that never enables AURA_SANDBOX_ENABLED.
for sandbox_root in /opt/aura-stack/deploy/sandboxes /opt/aura-stack/deploy/sandboxes-dev; do
    mkdir -p "$sandbox_root" && chmod 0710 "$sandbox_root"
done
ok "engineer-agent workspace roots created (used only when AURA_SANDBOX_ENABLED=true)"

timedatectl set-timezone UTC 2>/dev/null && ok "timezone UTC (log correlation across hosts)"
apt-get install -y -qq unattended-upgrades >/dev/null 2>&1 \
    && ok "unattended-upgrades installed (security patches)" \
    || info "unattended-upgrades not installed"

# -- 7. What to do next -------------------------------------------------------------------------

say "Ready"
cat <<EOF
    This host is prepared. The application is NOT deployed -- that is deliberate, because
    each tier has a verification step worth watching.

    Next, in order:

      1. Prove key-only SSH works from a NEW terminal before closing this one.

      2. Clone the repository (needs a deploy key authorised on GitHub):
             git clone git@github-aura:anasibrahimgnedy-collab/AURA-AI.git /opt/aura-stack

      3. Follow docs/guides/provisioning-runbook.md from section 2. Suggested for THIS host:
             AURA_WORKERS=$SUGGESTED_WORKERS

      4. Before decommissioning the old server, run pre-migration-backup.sh THERE and copy
         the result off. Contabo's terms, Clause 3(7), make backups your responsibility
         regardless of any backup product you buy from them.
EOF
