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
