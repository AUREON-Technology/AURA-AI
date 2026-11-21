# AURA web service: the public Desktop IDE install page (served at web.aureon-ai.tech).
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory, because the page serves the very file
# scripts/AURA-Desktop-Setup.cmd (one source of truth, no copy to keep in sync):
#
#   docker build -f aura_web/Dockerfile -t aura-web .
#   docker run --rm -e PORT=8080 -p 8080:8080 aura-web      # http://localhost:8080/desktop-ide/install
#
# On Railway: set RAILWAY_DOCKERFILE_PATH=aura_web/Dockerfile and leave the root directory empty.
#
# The finished app is part of the image. aura_web/build-assets/ is empty in git (only .gitkeep);
# .github/workflows/desktop-main-channel.yml builds the Windows installer on a Windows runner and
# drops `aura-desktop-windows-x64-setup.exe` and `latest.json` there before deploying, so a deploy
# replaces the installer and the manifest that describes it in one step. With no build yet the page
# says so and the downloads answer 404.
#
# Static files behind nginx, as the unprivileged user (uid 101). No Node, no database, no secrets.
# The base is pinned by digest (the 1.28-alpine index); update the tag and digest together.

FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8

USER root

COPY aura_web/site/ /usr/share/nginx/html/
COPY scripts/AURA-Desktop-Setup.cmd /usr/share/nginx/html/desktop-ide/download/AURA-Desktop-Setup.cmd
COPY aura_web/build-assets/ /usr/share/nginx/html/desktop-ide/download/
COPY aura_web/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY aura_web/nginx/default.conf.template /etc/nginx/templates/default.conf.template

# Put the served file's SHA-256 on the page, so the number can never disagree with the download.
# Then prove the config is valid at BUILD time: a typo fails the build instead of a deploy.
# `nginx -t` runs as root and leaves a root-owned /tmp/nginx.pid (and *_temp dirs) behind, which
# the unprivileged runtime user could not overwrite: nginx then dies at boot with "Permission
# denied" (found on the first Railway deploy). The last command removes them.
RUN set -eu; \
    sha="$(sha256sum /usr/share/nginx/html/desktop-ide/download/AURA-Desktop-Setup.cmd | cut -d' ' -f1)"; \
    test "${#sha}" -eq 64; \
    sed -i "s/__SETUP_SHA256__/${sha}/" /usr/share/nginx/html/desktop-ide/install.html; \
    ! grep -q '__SETUP_SHA256__' /usr/share/nginx/html/desktop-ide/install.html; \
    chmod -R a+rX /usr/share/nginx/html /etc/nginx/snippets; \
    PORT=8080 envsubst '${PORT}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf; \
    nginx -t; \
    rm /etc/nginx/conf.d/default.conf; \
    rm -rf /tmp/nginx.pid /tmp/*_temp

USER 101

# Railway injects PORT at run time; the entrypoint renders the template with it.
ENV PORT=8080
EXPOSE 8080
