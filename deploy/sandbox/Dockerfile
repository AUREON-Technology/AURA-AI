# Engineer-agent workspace image.
#
# Built from this repository rather than pulled at runtime, and that is a security decision, not
# packaging taste: with nothing to pull, the gateway never needs the Docker API's image
# endpoints, so `IMAGES` comes off docker-socket-proxy's allowlist (see
# deploy/docker-compose.prod.yml) and a compromised gateway is left with container operations
# alone. It also means the toolchain a workspace gets is reviewable in a diff instead of being
# whatever the upstream tag pointed at that morning.
#
# Pin by digest when you change the base. A tag is mutable, and this image runs model-chosen
# shell commands.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Set here rather than only in the compose file so the image is self-describing: anything
    # started from it goes through the egress proxy even if a caller forgets to pass these.
    # The compose file overrides them with the real proxy address.
    NO_PROXY=localhost,127.0.0.1

# git is the reason this image exists at all -- the agent works on real checkouts with real
# history. The rest is the minimum for a repository to be buildable and testable: a compiler for
# packages with C extensions, ca-certificates so HTTPS through the egress proxy validates, and
# node for the many repositories whose tests need it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        build-essential \
        curl \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Security scanners for the `scan_security` tool (ADR-050), pinned like everything else here: Semgrep and
# Bandit in their own virtualenv (so they cannot disturb a workspace's Python), Gitleaks as a release binary
# checked against its published sha256, and the Semgrep rule set as a checkout at one commit. The rules are
# bundled so a scan needs no network and no `--config auto` (which would phone home); the tool never reads
# rules from the scanned repository. Roughly +300 MB.
ARG SEMGREP_VERSION=1.187.0
ARG BANDIT_VERSION=1.9.4
ARG GITLEAKS_VERSION=8.28.0
ARG GITLEAKS_SHA256=a65b5253807a68ac0cafa4414031fd740aeb55f54fb7e55f386acb52e6a840eb
ARG SEMGREP_RULES_COMMIT=a5ad1714f00c9f7fde980cab0ea1d6dc6d414fd2
RUN python -m venv /opt/aura/scanners \
    && /opt/aura/scanners/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" "bandit==${BANDIT_VERSION}" \
    && curl -fsSL -o /tmp/gitleaks.tgz \
        "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    && echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tgz" | sha256sum -c - \
    && tar -xzf /tmp/gitleaks.tgz -C /usr/local/bin gitleaks \
    && rm /tmp/gitleaks.tgz \
    && git init -q /opt/aura/semgrep-rules \
    && git -C /opt/aura/semgrep-rules fetch -q --depth 1 https://github.com/semgrep/semgrep-rules "${SEMGREP_RULES_COMMIT}" \
    && git -C /opt/aura/semgrep-rules checkout -q FETCH_HEAD \
    && rm -rf /opt/aura/semgrep-rules/.git \
    && chmod -R a+rX /opt/aura
ENV PATH=/opt/aura/scanners/bin:$PATH \
    SEMGREP_SEND_METRICS=off \
    SEMGREP_ENABLE_VERSION_CHECK=0

# Unprivileged, and fixed rather than auto-assigned: the host directory bind-mounted at
# /workspace is chowned to this exact uid by deploy/scripts/bootstrap-host.sh, so the two have to
# agree. Root inside the container is root on a bind-mounted host directory, which is precisely
# what should not be true of a container running commands a model chose.
RUN groupadd --gid 10001 aura \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash aura

# HOME must be writable: git refuses to run without somewhere for its config, and pip/npm want a
# cache. The rootfs is mounted read-only at runtime, so these live on tmpfs mounts the compose
# file provides.
ENV HOME=/home/aura

WORKDIR /workspace
USER 10001:10001

# The container is a place to exec into, not a process to run. It idles until a tool calls
# `docker exec`, and stops when the workspace is paused.
CMD ["sleep", "infinity"]
