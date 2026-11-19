# Sandbox broker (audit F19): the policy-enforcing Docker API in front of docker-socket-proxy.
# See broker.py's docstring for what it forwards and why the proxy alone was not enough.
#
# Built from this repository, never pulled, like deploy/sandbox/Dockerfile -- and from the same
# digest-pinned base, so the host already has its layers. Standard library only: there is no
# dependency to pin or update beyond the base image itself.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY broker.py /app/broker.py

# The sandbox uid (10001), not root: the workspace root on the host is chowned to it with mode
# 0750 (bootstrap-host.sh), and the broker needs to traverse it -- read-only -- to resolve bind
# sources. It never writes anywhere; compose mounts the root read-only and the rootfs too.
USER 10001:10001

EXPOSE 2375
CMD ["python", "/app/broker.py"]
