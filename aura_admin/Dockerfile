FROM node:22-alpine AS deps
WORKDIR /app
# The lockfile pins what `npm audit` checked; `npm install` without it re-resolved every range at
# build time, so the deployed tree could differ from the audited one.
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Empty serves the console from the root of its own host (Railway: console.aureon-ai.tech). The
# VPS compose files pass /console, where nginx shares one host. Both values are baked in at build
# time: Next inlines NEXT_PUBLIC_* and the standalone server embeds basePath.
ARG NEXT_BASE_PATH=
ARG NEXT_PUBLIC_BASE_PATH=
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID
# Embedding needs nginx to proxy /grafana on the console's own host. Only the VPS stacks have that
# (their compose files pass 1); Railway has no such route, so the default must be off or every
# dashboard card renders a 404 frame. Optionally point the "Open in Grafana" links elsewhere
# (e.g. the Grafana Cloud stack URL) with NEXT_PUBLIC_GRAFANA_BASE_URL.
ARG NEXT_PUBLIC_GRAFANA_EMBED=0
ARG NEXT_PUBLIC_GRAFANA_BASE_URL=
ENV NEXT_PUBLIC_GRAFANA_BASE_URL=$NEXT_PUBLIC_GRAFANA_BASE_URL
ENV NEXT_BASE_PATH=$NEXT_BASE_PATH
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID
ENV NEXT_PUBLIC_GRAFANA_EMBED=$NEXT_PUBLIC_GRAFANA_EMBED
RUN npm run build

# Runner expects Next `output: "standalone"` (see next.config.ts). That emits
# .next/standalone (server.js + traced deps); static assets still copy separately.
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Same values the build used, so the runtime env never contradicts what was baked in.
ARG NEXT_BASE_PATH=
ARG NEXT_PUBLIC_BASE_PATH=
ENV NEXT_BASE_PATH=$NEXT_BASE_PATH
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
# The server runs as the image's unprivileged `node` user (uid 1000), not root (audit F52). It
# owns what it may write at runtime: Next creates .next/cache (image optimisation, fetch cache)
# under the standalone tree, so that tree is copied in owned by `node`.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
